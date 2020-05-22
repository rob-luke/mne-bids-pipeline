"""
===============
06. Apply ICA
===============

Blinks and ECG artifacts are automatically detected and the corresponding ICA
components are removed from the data.
This relies on the ICAs computed in 05-run_ica.py
!! If you manually add components to remove (config.rejcomps_man),
make sure you did not re-run the ICA in the meantime. Otherwise (especially if
the random state was not set, or you used a different machine, the component
order might differ).

"""

import os.path as op
import itertools
import logging

import mne
from mne.parallel import parallel_func
from mne.preprocessing import read_ica
from mne.preprocessing import create_eog_epochs, create_ecg_epochs
from mne.report import Report

from mne_bids import make_bids_basename

import numpy as np
import config
from config import gen_log_message, on_error, failsafe_run

logger = logging.getLogger('mne-study-template')


@failsafe_run(on_error=on_error)
def apply_ica(subject, run, session):
    deriv_path = config.get_subject_deriv_path(subject=subject,
                                               session=session,
                                               kind=config.get_kind())

    bids_basename = make_bids_basename(subject=subject,
                                       session=session,
                                       task=config.get_task(),
                                       acquisition=config.acq,
                                       run=None,
                                       processing=config.proc,
                                       recording=config.rec,
                                       space=config.space)

    fname_in = op.join(deriv_path, bids_basename + '-epo.fif')

    fname_out = op.join(deriv_path, bids_basename + '_cleaned-epo.fif')

    # load epochs to reject ICA components
    epochs = mne.read_epochs(fname_in, preload=True)

    msg = f'Input: {fname_in}, Output: {fname_out}'
    logger.info(gen_log_message(message=msg, step=5, subject=subject,
                                session=session))

    # load first run of raw data for ecg / eog epochs
    msg = 'Loading first run from raw data'
    logger.debug(gen_log_message(message=msg, step=5, subject=subject,
                                 session=session))

    bids_basename = make_bids_basename(subject=subject,
                                       session=session,
                                       task=config.get_task(),
                                       acquisition=config.acq,
                                       run=config.get_runs()[0],
                                       processing=config.proc,
                                       recording=config.rec,
                                       space=config.space
                                       )

    if config.use_maxwell_filter:
        raw_fname_in = op.join(deriv_path, bids_basename + '_sss_raw.fif')
    else:
        raw_fname_in = \
            op.join(deriv_path, bids_basename + '_filt_raw.fif')

    raw = mne.io.read_raw_fif(raw_fname_in, preload=True)

    # run ICA on MEG and EEG
    picks_meg = mne.pick_types(raw.info, meg=True, eeg=False,
                               eog=False, stim=False, exclude='bads')
    picks_eeg = mne.pick_types(raw.info, meg=False, eeg=True,
                               eog=False, stim=False, exclude='bads')
    all_picks = {'meg': picks_meg, 'eeg': picks_eeg}

    for ch_type in config.ch_types:
        report = None
        picks = all_picks[ch_type]

        # Load ICA
        fname_ica = op.join(deriv_path, f'bids_basename_{ch_type}-ica.fif')

        msg = f'Reading ICA: {fname_ica}'
        logger.debug(gen_log_message(message=msg, step=5, subject=subject,
                                     session=session))
        ica = read_ica(fname=fname_ica)

        pick_ecg = mne.pick_types(raw.info, meg=False, eeg=False,
                                  ecg=True, eog=False)

        # ECG
        # either needs an ecg channel, or avg of the mags (i.e. MEG data)
        ecg_inds = list()
        if pick_ecg or ch_type == 'meg':

            picks_ecg = np.concatenate([picks, pick_ecg])

            # Create ecg epochs
            if ch_type == 'meg':
                reject = {'mag': config.reject['mag'],
                          'grad': config.reject['grad']}
            elif ch_type == 'eeg':
                reject = {'eeg': config.reject['eeg']}

            ecg_epochs = create_ecg_epochs(raw, picks=picks_ecg, reject=reject,
                                           baseline=(None, 0), tmin=-0.5,
                                           tmax=0.5)

            ecg_average = ecg_epochs.average()

            ecg_inds, scores = \
                ica.find_bads_ecg(ecg_epochs, method='ctps',
                                  threshold=config.ica_ctps_ecg_threshold)
            del ecg_epochs

            report_fname = \
                op.join(deriv_path,
                        bids_basename + f'_{ch_type}-reject_ica.html')

            report = Report(report_fname, verbose=False)

            # Plot r score
            report.add_figs_to_section(ica.plot_scores(scores,
                                                       exclude=ecg_inds,
                                                       show=config.plot),
                                       captions=ch_type.upper() + ' - ECG - ' +
                                       'R scores')

            # Plot source time course
            report.add_figs_to_section(ica.plot_sources(ecg_average,
                                                        exclude=ecg_inds,
                                                        show=config.plot),
                                       captions=ch_type.upper() + ' - ECG - ' +
                                       'Sources time course')

            # Plot source time course
            report.add_figs_to_section(ica.plot_overlay(ecg_average,
                                                        exclude=ecg_inds,
                                                        show=config.plot),
                                       captions=ch_type.upper() + ' - ECG - ' +
                                       'Corrections')

        else:
            # XXX : to check when EEG only is processed
            msg = ('No ECG channel is present. Cannot automate IC detection '
                   'for ECG')
            logger.info(gen_log_message(message=msg, step=5, subject=subject,
                                        session=session))

        # EOG
        pick_eog = mne.pick_types(raw.info, meg=False, eeg=False,
                                  ecg=False, eog=True)
        eog_inds = list()
        if pick_eog.any():
            msg = 'Using EOG channel'
            logger.debug(gen_log_message(message=msg, step=5, subject=subject,
                                         session=session))

            picks_eog = np.concatenate([picks, pick_eog])
            # Create eog epochs
            eog_epochs = create_eog_epochs(raw, picks=picks_eog, reject=None,
                                           baseline=(None, 0), tmin=-0.5,
                                           tmax=0.5)

            eog_average = eog_epochs.average()
            eog_inds, scores = ica.find_bads_eog(eog_epochs, threshold=3.0)
            del eog_epochs

            params = dict(exclude=eog_inds, show=config.plot)

            # Plot r score
            report.add_figs_to_section(ica.plot_scores(scores, **params),
                                       captions=ch_type.upper() + ' - EOG - ' +
                                       'R scores')

            # Plot source time course
            report.add_figs_to_section(ica.plot_sources(eog_average, **params),
                                       captions=ch_type.upper() + ' - EOG - ' +
                                       'Sources time course')

            # Plot source time course
            report.add_figs_to_section(ica.plot_overlay(eog_average, **params),
                                       captions=ch_type.upper() + ' - EOG - ' +
                                       'Corrections')

            report.save(report_fname, overwrite=True, open_browser=False)

        else:
            msg = ('No EOG channel is present. Cannot automate IC detection '
                   'for EOG')
            logger.info(gen_log_message(message=msg, step=5, subject=subject,
                                        session=session))

        ica_reject = (list(ecg_inds) + list(eog_inds) +
                      list(config.rejcomps_man[subject][ch_type]))

        # now reject the components
        msg = f'Rejecting from {ch_type}: {ica_reject}'
        logger.info(gen_log_message(message=msg, step=5, subject=subject,
                                    session=session))
        epochs = ica.apply(epochs, exclude=ica_reject)

        msg = 'Saving cleaned epochs'
        logger.info(gen_log_message(message=msg, step=5, subject=subject,
                                    session=session))
        epochs.save(fname_out)

        if report is not None:
            fig = ica.plot_overlay(raw, exclude=ica_reject, show=config.plot)
            report.add_figs_to_section(fig, captions=ch_type.upper() +
                                       ' - ALL(epochs) - Corrections')

        if config.plot:
            epochs.plot_image(combine='gfp', group_by='type', sigma=2.,
                              cmap="YlGnBu_r", show=config.plot)


def main():
    """Apply ICA."""
    if not config.use_ica:
        return

    msg = 'Running Step 4: Apply ICA'
    logger.info(gen_log_message(step=5, message=msg))

    parallel, run_func, _ = parallel_func(apply_ica, n_jobs=config.N_JOBS)
    parallel(run_func(subject, run, session) for subject, run, session in
             itertools.product(config.get_subjects(), config.get_runs(),
                               config.get_sessions()))

    msg = 'Completed Step 4: Apply ICA'
    logger.info(gen_log_message(step=5, message=msg))


if __name__ == '__main__':
    main()
