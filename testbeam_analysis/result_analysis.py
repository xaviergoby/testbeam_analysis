''' All functions creating results (e.g. efficiency, residuals, track density) from fitted tracks are listed here.'''
from __future__ import division

import logging
import re
from collections import Iterable
import os.path

import tables as tb
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from scipy.optimize import curve_fit
from scipy.stats import binned_statistic_2d

from testbeam_analysis.tools import plot_utils
from testbeam_analysis.tools import geometry_utils
from testbeam_analysis.tools import analysis_utils


def calculate_residuals(input_tracks_file, input_alignment_file, output_residuals_file, n_pixels, pixel_size, dut_names=None, use_duts=None, max_chi2=None, nbins_per_pixel=None, npixels_per_bin=None, force_prealignment=False, use_fit_limits=False, output_pdf=True, chunk_size=1000000):
    '''Takes the tracks and calculates residuals for selected DUTs in col, row direction.
    Parameters
    ----------
    input_tracks_file : string
        File name with the tracks table
    input_alignment_file : pytables file
        File name of the input aligment data
    output_residuals_file : pytables file
        File name of the output file with the residual data
    n_pixels : iterable of tuples
        One tuple per DUT describing the number of pixels in column, row direction
        e.g. for 2 DUTs: n_pixels = [(80, 336), (80, 336)]
    pixel_size : iterable of tuples
        One tuple per DUT describing the pixel dimension in um in column, row direction
        e.g. for 2 DUTs: pixel_size = [(250, 50), (250, 50)]
    use_duts : iterable
        The duts to calculate residuals for. If None all duts in the input_tracks_file are used
    max_chi2 : int, iterable, None
        Use only not heavily scattered tracks to increase track pointing resolution (cut on chi2).
        Cut can be a number and is used then for all DUTS or a list with a chi 2 cut for each DUT.
        If None no cut is applied.
    nbins_per_pixel : int
        Number of bins per pixel along the residual axis. Number is a positive integer or None to automatically set the binning.
    npixels_per_bin : int
        Number of pixels per bin along the position axis. Number is a positive integer or None to automatically set the binning.
    force_prealignment : boolean
        Take the prealignment, although if a coarse alignment is availale
    output_pdf : boolean. PDF pages object
        Set to True to create pdf plots with a file name output_residuals_file.pdf
        Set to None to show plots.
        Set to False to not create plots, saves a lot of time.
    chunk_size : integer
        The size of data in RAM
    Returns
    -------
    A list of residuals in column row. e.g.: [Col residual DUT 0, Row residual DUT 0, Col residual DUT 1, Row residual DUT 1, ...]
    '''
    logging.info('=== Calculate residuals ===')

    use_prealignment = True if force_prealignment else False

    with tb.open_file(input_alignment_file, mode="r") as in_file_h5:  # Open file with alignment data
        prealignment = in_file_h5.root.PreAlignment[:]
        try:
            fit_limits = in_file_h5.root.PreAlignment.attrs.fit_limits
        except AttributeError:
            # TODO: use propre exception
            logging.warning("Cannot find fit_limits attribute, set use_fit_limits to False")
            use_fit_limits = False
        n_duts = prealignment.shape[0]
        if not use_prealignment:
            try:
                alignment = in_file_h5.root.Alignment[:]
            except tb.exceptions.NodeError:
                use_prealignment = True

    if use_prealignment:
        logging.info('Use prealignment data')
    else:
        logging.info('Use alignment data')

    close_pdf = True
    if output_pdf is True:
        output_fig = PdfPages(output_residuals_file[:-3] + '.pdf')
    elif output_pdf is None:
        output_fig = None
    elif output_pdf is False:
        output_fig = False
    else:
        output_fig = output_pdf  # output_fig is PDFpages object
        close_pdf = False  # Externally handled object do not close it

    residuals = []

    if not isinstance(max_chi2, Iterable):
        max_chi2 = [max_chi2] * n_duts

    with tb.open_file(input_tracks_file, mode='r') as in_file_h5:
        with tb.open_file(output_residuals_file, mode='w') as out_file_h5:
            for node in in_file_h5.root:
                actual_dut = int(re.findall(r'\d+', node.name)[-1])
                if use_duts and actual_dut not in use_duts:
                    continue
                logging.debug('Calculate residuals for DUT %d', actual_dut)

                if use_fit_limits:
                    fit_limit_x_local, fit_limit_y_local = fit_limits[actual_dut][0], fit_limits[actual_dut][1]
                    if use_prealignment:
                        fit_limit_x, fit_limit_y, _ = geometry_utils.apply_alignment(
                            fit_limit_x_local,
                            fit_limit_y_local,
                            [0.0, 0.0],
                            dut_index=actual_dut,
                            prealignment=prealignment,
                            inverse=False)
                    else:  # Apply transformation from fine alignment information
                        fit_limit_x, fit_limit_y, _ = geometry_utils.apply_alignment(
                            fit_limit_x_local,
                            fit_limit_y_local,
                            [0.0, 0.0],
                            dut_index=actual_dut,
                            alignment=alignment,
                            inverse=False)
                else:
                    fit_limit_x_local = None
                    fit_limit_y_local = None
                    fit_limit_x = None
                    fit_limit_y = None

                print "fit_limit_x_local", fit_limit_x_local
                print "fit_limit_y_local", fit_limit_y_local
                print "fit_limit_x", fit_limit_x
                print "fit_limit_y", fit_limit_y

                initialize = True  # initialize the histograms
                for tracks_chunk, _ in analysis_utils.data_aligned_at_events(node, chunk_size=chunk_size):

                    if max_chi2[actual_dut]:
                        tracks_chunk = tracks_chunk[tracks_chunk['track_chi2'] <= max_chi2[actual_dut]]
                    selection = np.logical_and(~np.isnan(tracks_chunk['x_dut_%d' % actual_dut]), ~np.isnan(tracks_chunk['y_dut_%d' % actual_dut]))
                    tracks_chunk = tracks_chunk[selection]  # Take only tracks where actual dut has a hit, otherwise residual wrong

                    # Coordinates in global coordinate system (x, y, z)
                    hit_x, hit_y, hit_z = tracks_chunk['x_dut_%d' % actual_dut], tracks_chunk['y_dut_%d' % actual_dut], tracks_chunk['z_dut_%d' % actual_dut]
                    intersection_x, intersection_y, intersection_z = tracks_chunk['offset_0'], tracks_chunk['offset_1'], tracks_chunk['offset_2']

                    # Transform to local coordinate system
                    if use_prealignment:
                        hit_x_local, hit_y_local, hit_z_local = geometry_utils.apply_alignment(hit_x, hit_y, hit_z,
                                                                                               dut_index=actual_dut,
                                                                                               prealignment=prealignment,
                                                                                               inverse=True)
                        intersection_x_local, intersection_y_local, intersection_z_local = geometry_utils.apply_alignment(intersection_x, intersection_y, intersection_z,
                                                                                                                          dut_index=actual_dut,
                                                                                                                          prealignment=prealignment,
                                                                                                                          inverse=True)
                    else:  # Apply transformation from fine alignment information
                        hit_x_local, hit_y_local, hit_z_local = geometry_utils.apply_alignment(hit_x, hit_y, hit_z,
                                                                                               dut_index=actual_dut,
                                                                                               alignment=alignment,
                                                                                               inverse=True)
                        intersection_x_local, intersection_y_local, intersection_z_local = geometry_utils.apply_alignment(intersection_x, intersection_y, intersection_z,
                                                                                                                          dut_index=actual_dut,
                                                                                                                          alignment=alignment,
                                                                                                                          inverse=True)

                    if not np.allclose(hit_z_local, 0.0) or not np.allclose(intersection_z_local, 0.0):
                        logging.error('Hit z position = %s and z intersection %s', str(hit_z_local[:3]), str(intersection_z_local[:3]))
                        raise RuntimeError('The transformation to the local coordinate system did not give all z = 0. Wrong alignment used?')

                    limit_x_selection = np.ones_like(hit_x_local, dtype=np.bool)
                    if fit_limit_x_local is not None and np.isfinite(fit_limit_x_local.min()):
                        limit_x_selection &= hit_x_local >= fit_limit_x_local.min()
                    if fit_limit_x_local is not None and np.isfinite(fit_limit_x_local.max()):
                        limit_x_selection &= hit_x_local <= fit_limit_x_local.max()
                    if fit_limit_x is not None and np.isfinite(fit_limit_x.min()):
                        limit_x_selection &= hit_x >= fit_limit_x.min()
                    if fit_limit_x is not None and np.isfinite(fit_limit_x.max()):
                        limit_x_selection &= hit_x <= fit_limit_x.max()

                    limit_y_selection = np.ones_like(hit_x_local, dtype=np.bool)
                    if fit_limit_y_local is not None and np.isfinite(fit_limit_y_local.min()):
                        limit_y_selection &= hit_y_local >= fit_limit_y_local.min()
                    if fit_limit_y_local is not None and np.isfinite(fit_limit_y_local.max()):
                        limit_y_selection &= hit_y_local <= fit_limit_y_local.max()
                    if fit_limit_y is not None and np.isfinite(fit_limit_y.min()):
                        limit_y_selection &= hit_y >= fit_limit_y.min()
                    if fit_limit_y is not None and np.isfinite(fit_limit_y.max()):
                        limit_y_selection &= hit_y <= fit_limit_y.max()

                    limit_xy_selection = np.ones_like(hit_x_local, dtype=np.bool)
                    if fit_limit_x_local is not None and np.isfinite(fit_limit_x_local.min()):
                        limit_xy_selection &= hit_x_local >= fit_limit_x_local.min()
                    if fit_limit_x_local is not None and np.isfinite(fit_limit_x_local.max()):
                        limit_xy_selection &= hit_x_local <= fit_limit_x_local.max()
                    if fit_limit_x is not None and np.isfinite(fit_limit_x.min()):
                        limit_xy_selection &= hit_x >= fit_limit_x.min()
                    if fit_limit_x is not None and np.isfinite(fit_limit_x.max()):
                        limit_xy_selection &= hit_x <= fit_limit_x.max()
                    if fit_limit_y_local is not None and np.isfinite(fit_limit_y_local.min()):
                        limit_xy_selection &= hit_y_local >= fit_limit_y_local.min()
                    if fit_limit_y_local is not None and np.isfinite(fit_limit_y_local.max()):
                        limit_xy_selection &= hit_y_local <= fit_limit_y_local.max()
                    if fit_limit_y is not None and np.isfinite(fit_limit_y.min()):
                        limit_xy_selection &= hit_y >= fit_limit_y.min()
                    if fit_limit_y is not None and np.isfinite(fit_limit_y.max()):
                        limit_xy_selection &= hit_y <= fit_limit_y.max()

                    print "remaning hits", np.count_nonzero(selection), "of", selection.shape[0]
                    assert np.count_nonzero(selection) != 0
                    #print "limit x local", fit_limit_x_local
                    #print "hit x local", hit_x_local

                    hit_x_local_limit_x = hit_x_local[limit_x_selection]
                    hit_y_local_limit_x = hit_y_local[limit_x_selection]
                    hit_z_local_limit_x = hit_z_local[limit_x_selection]
                    intersection_x_local_limit_x = intersection_x_local[limit_x_selection]
                    intersection_y_local_limit_x = intersection_y_local[limit_x_selection]
                    intersection_z_local_limit_x = intersection_z_local[limit_x_selection]
                    hit_x_limit_x = hit_x[limit_x_selection]
                    hit_y_limit_x = hit_y[limit_x_selection]
                    hit_z_limit_x = hit_z[limit_x_selection]
                    intersection_x_limit_x = intersection_x[limit_x_selection]
                    intersection_y_limit_x = intersection_y[limit_x_selection]
                    intersection_z_limit_x = intersection_z[limit_x_selection]

                    hit_x_local_limit_y = hit_x_local[limit_y_selection]
                    hit_y_local_limit_y = hit_y_local[limit_y_selection]
                    hit_z_local_limit_y = hit_z_local[limit_y_selection]
                    intersection_x_local_limit_y = intersection_x_local[limit_y_selection]
                    intersection_y_local_limit_y = intersection_y_local[limit_y_selection]
                    intersection_z_local_limit_y = intersection_z_local[limit_y_selection]
                    hit_x_limit_y = hit_x[limit_y_selection]
                    hit_y_limit_y = hit_y[limit_y_selection]
                    hit_z_limit_y = hit_z[limit_y_selection]
                    intersection_x_limit_y = intersection_x[limit_y_selection]
                    intersection_y_limit_y = intersection_y[limit_y_selection]
                    intersection_z_limit_y = intersection_z[limit_y_selection]

                    hit_x_local_limit_xy = hit_x_local[limit_xy_selection]
                    hit_y_local_limit_xy = hit_y_local[limit_xy_selection]
                    hit_z_local_limit_xy = hit_z_local[limit_xy_selection]
                    intersection_x_local_limit_xy = intersection_x_local[limit_xy_selection]
                    intersection_y_local_limit_xy = intersection_y_local[limit_xy_selection]
                    intersection_z_local_limit_xy = intersection_z_local[limit_xy_selection]
                    hit_x_limit_xy = hit_x[limit_xy_selection]
                    hit_y_limit_xy = hit_y[limit_xy_selection]
                    hit_z_limit_xy = hit_z[limit_xy_selection]
                    intersection_x_limit_xy = intersection_x[limit_xy_selection]
                    intersection_y_limit_xy = intersection_y[limit_xy_selection]
                    intersection_z_limit_xy = intersection_z[limit_xy_selection]

                    difference_limit_x = np.column_stack((hit_x_limit_x, hit_y_limit_x, hit_z_limit_x)) - np.column_stack((intersection_x_limit_x, intersection_y_limit_x, intersection_z_limit_x))
                    difference_local_limit_x = np.column_stack((hit_x_local_limit_x, hit_y_local_limit_x, hit_z_local_limit_x)) - np.column_stack((intersection_x_local_limit_x, intersection_y_local_limit_x, intersection_z_local_limit_x))

                    difference_limit_y = np.column_stack((hit_x_limit_y, hit_y_limit_y, hit_z_limit_y)) - np.column_stack((intersection_x_limit_y, intersection_y_limit_y, intersection_z_limit_y))
                    difference_local_limit_y = np.column_stack((hit_x_local_limit_y, hit_y_local_limit_y, hit_z_local_limit_y)) - np.column_stack((intersection_x_local_limit_y, intersection_y_local_limit_y, intersection_z_local_limit_y))

                    difference_limit_xy = np.column_stack((hit_x_limit_xy, hit_y_limit_xy, hit_z_limit_xy)) - np.column_stack((intersection_x_limit_xy, intersection_y_limit_xy, intersection_z_limit_xy))
                    difference_local_limit_xy = np.column_stack((hit_x_local_limit_xy, hit_y_local_limit_xy, hit_z_local_limit_xy)) - np.column_stack((intersection_x_local_limit_xy, intersection_y_local_limit_xy, intersection_z_local_limit_xy))

                    difference = np.column_stack((hit_x, hit_y, hit_z)) - np.column_stack((intersection_x, intersection_y, intersection_z))
                    difference_local = np.column_stack((hit_x_local, hit_y_local, hit_z_local)) - np.column_stack((intersection_x_local, intersection_y_local, intersection_z_local))

                    # Histogram residuals in different ways
                    if initialize:  # Only true for the first iteration, calculate the binning for the histograms
                        initialize = False

                        # detect peaks and calculate width to estimate the size of the histograms
                        if nbins_per_pixel is not None:
                            min_difference, max_difference = np.min(difference[:, 0]), np.max(difference[:, 0])
                            nbins = np.arange(min_difference - (pixel_size[actual_dut][0] / nbins_per_pixel), max_difference + 2 * (pixel_size[actual_dut][0] / nbins_per_pixel), pixel_size[actual_dut][0] / nbins_per_pixel)
                        else:
                            nbins = "auto"
                        hist, edges = np.histogram(difference[:, 0], bins=nbins)
                        edge_center = (edges[1:] + edges[:-1]) / 2.0
                        try:
                            _, center_x, fwhm_x, _ = analysis_utils.peak_detect(edge_center, hist)
                        except RuntimeError:
                            # do some simple FWHM with numpy array
                            _, center_x, fwhm_x, _ = analysis_utils.simple_peak_detect(edge_center, hist)

                        if nbins_per_pixel is not None:
                            min_difference, max_difference = np.min(difference[:, 1]), np.max(difference[:, 1])
                            nbins = np.arange(min_difference - (pixel_size[actual_dut][1] / nbins_per_pixel), max_difference + 2 * (pixel_size[actual_dut][1] / nbins_per_pixel), pixel_size[actual_dut][1] / nbins_per_pixel)
                        else:
                            nbins = "auto"
                        hist, edges = np.histogram(difference[:, 1], bins=nbins)
                        edge_center = (edges[1:] + edges[:-1]) / 2.0
                        try:
                            _, center_y, fwhm_y, _ = analysis_utils.peak_detect(edge_center, hist)
                        except RuntimeError:
                            # do some simple FWHM with numpy array
                            _, center_y, fwhm_y, _ = analysis_utils.simple_peak_detect(edge_center, hist)

                        if nbins_per_pixel is not None:
                            min_difference, max_difference = np.min(difference_local[:, 0]), np.max(difference_local[:, 0])
                            nbins = np.arange(min_difference - (pixel_size[actual_dut][0] / nbins_per_pixel), max_difference + 2 * (pixel_size[actual_dut][0] / nbins_per_pixel), pixel_size[actual_dut][0] / nbins_per_pixel)
                        else:
                            nbins = "auto"
                        hist, edges = np.histogram(difference_local[:, 0], bins=nbins)
                        edge_center = (edges[1:] + edges[:-1]) / 2.0
                        try:
                            _, center_col, fwhm_col, _ = analysis_utils.peak_detect(edge_center, hist)
                        except RuntimeError:
                            # do some simple FWHM with numpy array
                            _, center_col, fwhm_col, _ = analysis_utils.simple_peak_detect(edge_center, hist)

                        if nbins_per_pixel is not None:
                            min_difference, max_difference = np.min(difference_local[:, 1]), np.max(difference_local[:, 1])
                            nbins = np.arange(min_difference - (pixel_size[actual_dut][1] / nbins_per_pixel), max_difference + 2 * (pixel_size[actual_dut][1] / nbins_per_pixel), pixel_size[actual_dut][1] / nbins_per_pixel)
                        else:
                            nbins = "auto"
                        hist, edges = np.histogram(difference_local[:, 1], bins=nbins)
                        edge_center = (edges[1:] + edges[:-1]) / 2.0
                        try:
                            _, center_row, fwhm_row, _ = analysis_utils.peak_detect(edge_center, hist)
                        except RuntimeError:
                            # do some simple FWHM with numpy array
                            _, center_row, fwhm_row, _ = analysis_utils.simple_peak_detect(edge_center, hist)

                        # calculate the binning of the histograms, the minimum size is given by plot_npixels, otherwise FWHM is taken into account
                        plot_npixels = 6.0
                        if nbins_per_pixel is not None:
                            width = max(plot_npixels * pixel_size[actual_dut][0], pixel_size[actual_dut][0] * np.ceil(plot_npixels * fwhm_x / pixel_size[actual_dut][0]))
                            if np.mod(width / pixel_size[actual_dut][0], 2) != 0:
                                width += pixel_size[actual_dut][0]
                            nbins = int(nbins_per_pixel * width / pixel_size[actual_dut][0])
                            x_range = (center_x - 0.5 * width, center_x + 0.5 * width)
                        else:
                            nbins = "auto"
                            width = pixel_size[actual_dut][0] * np.ceil(plot_npixels * fwhm_x / pixel_size[actual_dut][0])
                            x_range = (center_x - width, center_x + width)
                        hist_residual_x_hist, hist_residual_x_xedges = np.histogram(difference_limit_xy[:, 0], range=x_range, bins=nbins)

                        if npixels_per_bin is not None:
                            min_intersection, max_intersection = np.min(intersection_x), np.max(intersection_x)
                            nbins = np.arange(min_intersection, max_intersection + npixels_per_bin * pixel_size[actual_dut][1], npixels_per_bin * pixel_size[actual_dut][1])
                        else:
                            nbins = "auto"
                        _, hist_residual_x_yedges = np.histogram(intersection_x, bins=nbins)

                        if nbins_per_pixel is not None:
                            width = max(plot_npixels * pixel_size[actual_dut][1], pixel_size[actual_dut][1] * np.ceil(plot_npixels * fwhm_x / pixel_size[actual_dut][1]))
                            if np.mod(width / pixel_size[actual_dut][0], 2) != 0:
                                width += pixel_size[actual_dut][1]
                            nbins = int(nbins_per_pixel * width / pixel_size[actual_dut][1])
                            y_range = (center_y - 0.5 * width, center_y + 0.5 * width)
                        else:
                            nbins = "auto"
                            width = pixel_size[actual_dut][1] * np.ceil(plot_npixels * fwhm_y / pixel_size[actual_dut][1])
                            y_range = (center_y - width, center_y + width)
                        hist_residual_y_hist, hist_residual_y_yedges = np.histogram(difference_limit_xy[:, 1], range=y_range, bins=nbins)

                        if npixels_per_bin is not None:
                            min_intersection, max_intersection = np.min(intersection_y), np.max(intersection_y)
                            nbins = np.arange(min_intersection, max_intersection + npixels_per_bin * pixel_size[actual_dut][0], npixels_per_bin * pixel_size[actual_dut][0])
                        else:
                            nbins = "auto"
                        _, hist_residual_y_xedges = np.histogram(intersection_y, bins=nbins)

                        if nbins_per_pixel is not None:
                            width = max(plot_npixels * pixel_size[actual_dut][0], pixel_size[actual_dut][0] * np.ceil(plot_npixels * fwhm_x / pixel_size[actual_dut][0]))
                            if np.mod(width / pixel_size[actual_dut][0], 2) != 0:
                                width += pixel_size[actual_dut][0]
                            nbins = int(nbins_per_pixel * width / pixel_size[actual_dut][0])
                            col_range = (center_col - 0.5 * width, center_col + 0.5 * width)
                        else:
                            nbins = "auto"
                            width = pixel_size[actual_dut][0] * np.ceil(plot_npixels * fwhm_col / pixel_size[actual_dut][0])
                            col_range = (center_col - width, center_col + width)
                        hist_residual_col_hist, hist_residual_col_xedges = np.histogram(difference_local_limit_xy[:, 0], range=col_range, bins=nbins)

                        if npixels_per_bin is not None:
                            min_intersection, max_intersection = np.min(intersection_x_local), np.max(intersection_x_local)
                            nbins = np.arange(min_intersection, max_intersection + npixels_per_bin * pixel_size[actual_dut][1], npixels_per_bin * pixel_size[actual_dut][1])
                        else:
                            nbins = "auto"
                        _, hist_residual_col_yedges = np.histogram(intersection_x_local, bins=nbins)

                        if nbins_per_pixel is not None:
                            width = max(plot_npixels * pixel_size[actual_dut][1], pixel_size[actual_dut][1] * np.ceil(plot_npixels * fwhm_x / pixel_size[actual_dut][1]))
                            if np.mod(width / pixel_size[actual_dut][0], 2) != 0:
                                width += pixel_size[actual_dut][1]
                            nbins = int(nbins_per_pixel * width / pixel_size[actual_dut][1])
                            row_range = (center_row - 0.5 * width, center_row + 0.5 * width)
                        else:
                            nbins = "auto"
                            width = pixel_size[actual_dut][1] * np.ceil(plot_npixels * fwhm_row / pixel_size[actual_dut][1])
                            row_range = (center_row - width, center_row + width)
                        hist_residual_row_hist, hist_residual_row_yedges = np.histogram(difference_local_limit_xy[:, 1], range=row_range, bins=nbins)

                        if npixels_per_bin is not None:
                            min_intersection, max_intersection = np.min(intersection_y_local), np.max(intersection_y_local)
                            nbins = np.arange(min_intersection, max_intersection + npixels_per_bin * pixel_size[actual_dut][0], npixels_per_bin * pixel_size[actual_dut][0])
                        else:
                            nbins = "auto"
                        _, hist_residual_row_xedges = np.histogram(intersection_y_local, bins=nbins)

                        # global x residual against x position
                        hist_x_residual_x_hist, hist_x_residual_x_xedges, hist_x_residual_x_yedges = np.histogram2d(
                            intersection_x,
                            difference[:, 0],
                            bins=(hist_residual_x_yedges, hist_residual_x_xedges))

                        # global y residual against y position
                        hist_y_residual_y_hist, hist_y_residual_y_xedges, hist_y_residual_y_yedges = np.histogram2d(
                            intersection_y,
                            difference[:, 1],
                            bins=(hist_residual_y_xedges, hist_residual_y_yedges))

                        # global y residual against x position
                        hist_x_residual_y_hist, hist_x_residual_y_xedges, hist_x_residual_y_yedges = np.histogram2d(
                            intersection_x_limit_y,
                            difference_limit_y[:, 1],
                            bins=(hist_residual_x_yedges, hist_residual_y_yedges))

                        # global x residual against y position
                        hist_y_residual_x_hist, hist_y_residual_x_xedges, hist_y_residual_x_yedges = np.histogram2d(
                            intersection_y_limit_x,
                            difference_limit_x[:, 0],
                            bins=(hist_residual_y_xedges, hist_residual_x_xedges))

                        # local column residual against column position
                        hist_col_residual_col_hist, hist_col_residual_col_xedges, hist_col_residual_col_yedges = np.histogram2d(
                            intersection_x_local,
                            difference_local[:, 0],
                            bins=(hist_residual_col_yedges, hist_residual_col_xedges))

                        # local row residual against row position
                        hist_row_residual_row_hist, hist_row_residual_row_xedges, hist_row_residual_row_yedges = np.histogram2d(
                            intersection_y_local,
                            difference_local[:, 1],
                            bins=(hist_residual_row_xedges, hist_residual_row_yedges))

                        # local row residual against column position
                        hist_col_residual_row_hist, hist_col_residual_row_xedges, hist_col_residual_row_yedges = np.histogram2d(
                            intersection_x_local_limit_y,
                            difference_local_limit_y[:, 1],
                            bins=(hist_residual_col_yedges, hist_residual_row_yedges))

                        # local column residual against row position
                        hist_row_residual_col_hist, hist_row_residual_col_xedges, hist_row_residual_col_yedges = np.histogram2d(
                            intersection_y_local_limit_x,
                            difference_local_limit_x[:, 0],
                            bins=(hist_residual_row_xedges, hist_residual_col_xedges))

                    else:  # adding data to existing histograms
                        hist_residual_x_hist += np.histogram(difference_limit_xy[:, 0], bins=hist_residual_x_xedges)[0]
                        hist_residual_y_hist += np.histogram(difference_limit_xy[:, 1], bins=hist_residual_y_yedges)[0]
                        hist_residual_col_hist += np.histogram(difference_local_limit_xy[:, 0], bins=hist_residual_col_xedges)[0]
                        hist_residual_row_hist += np.histogram(difference_local_limit_xy[:, 1], bins=hist_residual_row_yedges)[0]

                        # global x residual against x position
                        hist_x_residual_x_hist += np.histogram2d(
                            intersection_x,
                            difference[:, 0],
                            bins=(hist_x_residual_x_xedges, hist_x_residual_x_yedges))[0]

                        # global y residual against y position
                        hist_y_residual_y_hist += np.histogram2d(
                            intersection_y,
                            difference[:, 1],
                            bins=(hist_y_residual_y_xedges, hist_y_residual_y_yedges))[0]

                        # global y residual against x position
                        hist_x_residual_y_hist += np.histogram2d(
                            intersection_x_limit_y,
                            difference_limit_y[:, 1],
                            bins=(hist_x_residual_y_xedges, hist_x_residual_y_yedges))[0]

                        # global x residual against y position
                        hist_y_residual_x_hist += np.histogram2d(
                            intersection_y_limit_x,
                            difference_limit_x[:, 0],
                            bins=(hist_y_residual_x_xedges, hist_y_residual_x_yedges))[0]

                        # local column residual against column position
                        hist_col_residual_col_hist += np.histogram2d(
                            intersection_x_local,
                            difference_local[:, 0],
                            bins=(hist_col_residual_col_xedges, hist_col_residual_col_yedges))[0]

                        # local row residual against row position
                        hist_row_residual_row_hist += np.histogram2d(
                            intersection_y_local,
                            difference_local[:, 1],
                            bins=(hist_row_residual_row_xedges, hist_row_residual_row_yedges))[0]

                        # local row residual against column position
                        hist_col_residual_row_hist += np.histogram2d(
                            intersection_x_local_limit_y,
                            difference_local_limit_y[:, 1],
                            bins=(hist_col_residual_row_xedges, hist_col_residual_row_yedges))[0]

                        # local column residual against row position
                        hist_row_residual_col_hist += np.histogram2d(
                            intersection_y_local_limit_x,
                            difference_local_limit_x[:, 0],
                            bins=(hist_row_residual_col_xedges, hist_row_residual_col_yedges))[0]

                logging.debug('Storing residual histograms...')

                dut_name = dut_names[actual_dut] if dut_names else ("DUT " + str(actual_dut))
                # Global residuals
                fit_residual_x, cov_residual_x = analysis_utils.fit_residuals(
                    hist=hist_residual_x_hist,
                    edges=hist_residual_x_xedges,
                    label='X residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig
                )
                out_res_x = out_file_h5.createCArray(out_file_h5.root,
                                                     name='ResidualsX_DUT%d' % (actual_dut),
                                                     title='Residual distribution in x direction for %s' % (dut_name),
                                                     atom=tb.Atom.from_dtype(hist_residual_x_hist.dtype),
                                                     shape=hist_residual_x_hist.shape,
                                                     filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_res_x.attrs.xedges = hist_residual_x_xedges
                out_res_x.attrs.fit_coeff = fit_residual_x
                out_res_x.attrs.fit_cov = cov_residual_x
                out_res_x[:] = hist_residual_x_hist

                fit_residual_y, cov_residual_y = analysis_utils.fit_residuals(
                    hist=hist_residual_y_hist,
                    edges=hist_residual_y_yedges,
                    label='Y residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig
                )
                out_res_y = out_file_h5.createCArray(out_file_h5.root,
                                                     name='ResidualsY_DUT%d' % (actual_dut),
                                                     title='Residual distribution in y direction for %s' % (dut_name),
                                                     atom=tb.Atom.from_dtype(hist_residual_y_hist.dtype),
                                                     shape=hist_residual_y_hist.shape,
                                                     filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_res_y.attrs.yedges = hist_residual_y_yedges
                out_res_y.attrs.fit_coeff = fit_residual_y
                out_res_y.attrs.fit_cov = cov_residual_y
                out_res_y[:] = hist_residual_y_hist

                fit_x_residual_x, cov_x_residual_x = analysis_utils.fit_residuals_vs_position(
                    hist=hist_x_residual_x_hist,
                    xedges=hist_x_residual_x_xedges,
                    yedges=hist_x_residual_x_yedges,
                    xlabel='X position [um]',
                    ylabel='X residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig,
                    fit_limit=fit_limit_x
                )
                
                out_x_res_x = out_file_h5.createCArray(out_file_h5.root,
                                                       name='XResidualsX_DUT%d' % (actual_dut),
                                                       title='Residual distribution in x direction as a function of the x position for %s' % (dut_name),
                                                       atom=tb.Atom.from_dtype(hist_x_residual_x_hist.dtype),
                                                       shape=hist_x_residual_x_hist.shape,
                                                       filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_x_res_x.attrs.xedges = hist_x_residual_x_xedges
                out_x_res_x.attrs.yedges = hist_x_residual_x_yedges
                out_x_res_x.attrs.fit_coeff = fit_x_residual_x
                out_x_res_x.attrs.fit_cov = cov_x_residual_x
                out_x_res_x[:] = hist_x_residual_x_hist

                fit_y_residual_y, cov_y_residual_y = analysis_utils.fit_residuals_vs_position(
                    hist=hist_y_residual_y_hist,
                    xedges=hist_y_residual_y_xedges,
                    yedges=hist_y_residual_y_yedges,
                    xlabel='Y position [um]',
                    ylabel='Y residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig,
                    fit_limit=fit_limit_y
                )
                out_y_res_y = out_file_h5.createCArray(out_file_h5.root,
                                                       name='YResidualsY_DUT%d' % (actual_dut),
                                                       title='Residual distribution in y direction as a function of the y position for %s' % (dut_name),
                                                       atom=tb.Atom.from_dtype(hist_y_residual_y_hist.dtype),
                                                       shape=hist_y_residual_y_hist.shape,
                                                       filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_y_res_y.attrs.xedges = hist_y_residual_y_xedges
                out_y_res_y.attrs.yedges = hist_y_residual_y_yedges
                out_y_res_y.attrs.fit_coeff = fit_y_residual_y
                out_y_res_y.attrs.fit_cov = cov_y_residual_y
                out_y_res_y[:] = hist_y_residual_y_hist

                fit_x_residual_y, cov_x_residual_y = analysis_utils.fit_residuals_vs_position(
                    hist=hist_x_residual_y_hist,
                    xedges=hist_x_residual_y_xedges,
                    yedges=hist_x_residual_y_yedges,
                    xlabel='X position [um]',
                    ylabel='Y residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig,
                    fit_limit=fit_limit_x
                )
                out_x_res_y = out_file_h5.createCArray(out_file_h5.root,
                                                       name='XResidualsY_DUT%d' % (actual_dut),
                                                       title='Residual distribution in y direction as a function of the x position for %s' % (dut_name),
                                                       atom=tb.Atom.from_dtype(hist_x_residual_y_hist.dtype),
                                                       shape=hist_x_residual_y_hist.shape,
                                                       filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_x_res_y.attrs.xedges = hist_x_residual_y_xedges
                out_x_res_y.attrs.yedges = hist_x_residual_y_yedges
                out_x_res_y.attrs.fit_coeff = fit_x_residual_y
                out_x_res_y.attrs.fit_cov = cov_x_residual_y
                out_x_res_y[:] = hist_x_residual_y_hist

                fit_y_residual_x, cov_y_residual_x = analysis_utils.fit_residuals_vs_position(
                    hist=hist_y_residual_x_hist,
                    xedges=hist_y_residual_x_xedges,
                    yedges=hist_y_residual_x_yedges,
                    xlabel='Y position [um]',
                    ylabel='X residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig,
                    fit_limit=fit_limit_y
                )
                out_y_res_x = out_file_h5.createCArray(out_file_h5.root,
                                                       name='YResidualsX_DUT%d' % (actual_dut),
                                                       title='Residual distribution in x direction as a function of the y position for %s' % (dut_name),
                                                       atom=tb.Atom.from_dtype(hist_y_residual_x_hist.dtype),
                                                       shape=hist_y_residual_x_hist.shape,
                                                       filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_y_res_x.attrs.xedges = hist_y_residual_x_xedges
                out_y_res_x.attrs.yedges = hist_y_residual_x_yedges
                out_y_res_x.attrs.fit_coeff = fit_y_residual_x
                out_y_res_x.attrs.fit_cov = cov_y_residual_x
                out_y_res_x[:] = hist_y_residual_x_hist

                # Local residuals
                fit_residual_col, cov_residual_col = analysis_utils.fit_residuals(
                    hist=hist_residual_col_hist,
                    edges=hist_residual_col_xedges,
                    label='Column residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig
                )
                out_res_col = out_file_h5.createCArray(out_file_h5.root,
                                                       name='ResidualsCol_DUT%d' % (actual_dut),
                                                       title='Residual distribution in column direction for %s' % (dut_name),
                                                       atom=tb.Atom.from_dtype(hist_residual_col_hist.dtype),
                                                       shape=hist_residual_col_hist.shape,
                                                       filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_res_col.attrs.xedges = hist_residual_col_xedges
                out_res_col.attrs.fit_coeff = fit_residual_col
                out_res_col.attrs.fit_cov = cov_residual_col
                out_res_col[:] = hist_residual_col_hist

                fit_residual_row, cov_residual_row = analysis_utils.fit_residuals(
                    hist=hist_residual_row_hist,
                    edges=hist_residual_row_yedges,
                    label='Row residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig
                )
                out_res_row = out_file_h5.createCArray(out_file_h5.root,
                                                       name='ResidualsRow_DUT%d' % (actual_dut),
                                                       title='Residual distribution in row direction for %s' % (dut_name),
                                                       atom=tb.Atom.from_dtype(hist_residual_row_hist.dtype),
                                                       shape=hist_residual_row_hist.shape,
                                                       filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_res_row.attrs.yedges = hist_residual_row_yedges
                out_res_row.attrs.fit_coeff = fit_residual_row
                out_res_row.attrs.fit_cov = cov_residual_row
                out_res_row[:] = hist_residual_row_hist

                fit_col_residual_col, cov_col_residual_col = analysis_utils.fit_residuals_vs_position(
                    hist=hist_col_residual_col_hist,
                    xedges=hist_col_residual_col_xedges,
                    yedges=hist_col_residual_col_yedges,
                    xlabel='Column position [um]',
                    ylabel='Column residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig,
                    fit_limit=fit_limit_x_local
                )
                out_col_res_col = out_file_h5.createCArray(out_file_h5.root,
                                                           name='ColResidualsCol_DUT%d' % (actual_dut),
                                                           title='Residual distribution in column direction as a function of the column position for %s' % (dut_name),
                                                           atom=tb.Atom.from_dtype(hist_col_residual_col_hist.dtype),
                                                           shape=hist_col_residual_col_hist.shape,
                                                           filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_col_res_col.attrs.xedges = hist_col_residual_col_xedges
                out_col_res_col.attrs.yedges = hist_col_residual_col_yedges
                out_col_res_col.attrs.fit_coeff = fit_col_residual_col
                out_col_res_col.attrs.fit_cov = cov_col_residual_col
                out_col_res_col[:] = hist_col_residual_col_hist

                fit_row_residual_row, cov_row_residual_row = analysis_utils.fit_residuals_vs_position(
                    hist=hist_row_residual_row_hist,
                    xedges=hist_row_residual_row_xedges,
                    yedges=hist_row_residual_row_yedges,
                    xlabel='Row position [um]',
                    ylabel='Row residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig,
                    fit_limit=fit_limit_y_local
                )
                out_row_res_row = out_file_h5.createCArray(out_file_h5.root,
                                                           name='RowResidualsRow_DUT%d' % (actual_dut),
                                                           title='Residual distribution in row direction as a function of the row position for %s' % (dut_name),
                                                           atom=tb.Atom.from_dtype(hist_row_residual_row_hist.dtype),
                                                           shape=hist_row_residual_row_hist.shape,
                                                           filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_row_res_row.attrs.xedges = hist_row_residual_row_xedges
                out_row_res_row.attrs.yedges = hist_row_residual_row_yedges
                out_row_res_row.attrs.fit_coeff = fit_row_residual_row
                out_row_res_row.attrs.fit_cov = cov_row_residual_row
                out_row_res_row[:] = hist_row_residual_row_hist

                fit_col_residual_row, cov_col_residual_row = analysis_utils.fit_residuals_vs_position(
                    hist=hist_col_residual_row_hist,
                    xedges=hist_col_residual_row_xedges,
                    yedges=hist_col_residual_row_yedges,
                    xlabel='Column position [um]',
                    ylabel='Row residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig,
                    fit_limit=fit_limit_x_local
                )
                out_col_res_row = out_file_h5.createCArray(out_file_h5.root,
                                                           name='ColResidualsRow_DUT%d' % (actual_dut),
                                                           title='Residual distribution in row direction as a function of the column position for %s' % (dut_name),
                                                           atom=tb.Atom.from_dtype(hist_col_residual_row_hist.dtype),
                                                           shape=hist_col_residual_row_hist.shape,
                                                           filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_col_res_row.attrs.xedges = hist_col_residual_row_xedges
                out_col_res_row.attrs.yedges = hist_col_residual_row_yedges
                out_col_res_row.attrs.fit_coeff = fit_col_residual_row
                out_col_res_row.attrs.fit_cov = cov_col_residual_row
                out_col_res_row[:] = hist_col_residual_row_hist

                fit_row_residual_col, cov_row_residual_col = analysis_utils.fit_residuals_vs_position(
                    hist=hist_row_residual_col_hist,
                    xedges=hist_row_residual_col_xedges,
                    yedges=hist_row_residual_col_yedges,
                    xlabel='Row position [um]',
                    ylabel='Column residual [um]',
                    title='Residuals for %s' % (dut_name),
                    output_fig=output_fig,
                    fit_limit=fit_limit_y_local
                )
                out_row_res_col = out_file_h5.createCArray(out_file_h5.root,
                                                           name='RowResidualsCol_DUT%d' % (actual_dut),
                                                           title='Residual distribution in column direction as a function of the row position for %s' % (dut_name),
                                                           atom=tb.Atom.from_dtype(hist_row_residual_col_hist.dtype),
                                                           shape=hist_row_residual_col_hist.shape,
                                                           filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                out_row_res_col.attrs.xedges = hist_row_residual_col_xedges
                out_row_res_col.attrs.yedges = hist_row_residual_col_yedges
                out_row_res_col.attrs.fit_coeff = fit_row_residual_col
                out_row_res_col.attrs.fit_cov = cov_row_residual_col
                out_row_res_col[:] = hist_row_residual_col_hist

    if output_fig and close_pdf:
        output_fig.close()


def calculate_efficiency(input_tracks_file, input_alignment_file, output_efficiency_file, bin_size, pixel_size, n_pixels, dut_names=None, sensor_sizes=None, minimum_tracks_per_bin=0, use_duts=None, max_chi2=None, force_prealignment=False, cut_distance=None, max_distance=1000, charge_bins=None, dut_masks=None, col_range=None, row_range=None, efficiency_range=None, show_inefficient_events=False, chunk_size=1000000):
    '''Takes the tracks and calculates the hit efficiency and hit/track hit distance for selected DUTs.
    Parameters
    ----------
    input_tracks_file : string
        file name with the tracks table
    input_alignment_file : pytables file
        File name of the input aligment data
    output_efficiency_file : outout file name
    bin_size : iterable
        sizes of bins (i.e. (virtual) pixel size). Give one tuple (x, y) for every plane or list of tuples for different planes
    sensor_sizes : Tuple or list of tuples
        Describes the sensor size for each DUT. If one tuple is given it is (size x, size y)
        If several tuples are given it is [(DUT0 size x, DUT0 size y), (DUT1 size x, DUT1 size y), ...]
    minimum_tracks_per_bin : int
        minimum number of tracks required to consider bin for efficiency calculation
    use_duts : iterable
        the DUTs to calculate efficiency for. If None all duts are used
    max_chi2 : int
        only use track with a chi2 <= max_chi2
    force_prealignment : boolean
        Take the prealignment, although if a coarse alignment is availale
    cut_distance : int
        use only distances (between DUT hit and track hit) smaller than cut_distance
    max_distance : int
        defines binnig of distance values
    col_range, row_range : iterable
        column / row value to calculate efficiency for (to neglect noisy edge pixels for efficiency calculation)
    chunk_size : integer
        The size of data in RAM
    '''
    logging.info('=== Calculate efficiency ===')

    use_prealignment = True if force_prealignment else False
    sensor_sizes = [sensor_sizes, ] if not isinstance(sensor_sizes, Iterable) else sensor_size  # Sensor dimensions for each DUT

    with tb.open_file(input_alignment_file, mode="r") as in_file_h5:  # Open file with alignment data
        prealignment = in_file_h5.root.PreAlignment[:]
        n_duts = prealignment.shape[0]
        if not use_prealignment:
            try:
                alignment = in_file_h5.root.Alignment[:]
                logging.info('Use alignment data')
            except tb.exceptions.NodeError:
                use_prealignment = True
                logging.info('Use prealignment data')
    use_duts = use_duts if use_duts is not None else range(n_duts)  # standard setting: fit tracks for all DUTs

    if not isinstance(max_chi2, Iterable):
        max_chi2 = [max_chi2] * len(use_duts)

    if not isinstance(cut_distance, Iterable):
        cut_distance = [cut_distance] * len(use_duts)

    if not isinstance(max_distance, Iterable):
        max_distance = [max_distance] * len(use_duts)

    if not isinstance(charge_bins, Iterable):
        charge_bins = [charge_bins] * len(use_duts)

    if not isinstance(dut_masks, Iterable):
        dut_masks = [dut_masks] * len(use_duts)

    if not isinstance(efficiency_range, Iterable):
        efficiency_range = [efficiency_range] * len(use_duts)

    output_pdf_file = os.path.splitext(output_efficiency_file)[0] + '.pdf'

    with PdfPages(output_pdf_file) as output_fig:
        efficiencies = []
        pass_tracks = []
        total_tracks = []
        with tb.open_file(output_efficiency_file, 'w') as out_file_h5:
            with tb.open_file(input_tracks_file, mode='r') as in_file_h5:
                for node in in_file_h5.root:
                    actual_dut = int(re.findall(r'\d+', node.name)[-1])
    
                    if use_duts and actual_dut not in use_duts:
                        continue
                    dut_index = np.where(np.array(use_duts) == actual_dut)[0][0]
                    print "actual_dut", actual_dut
                    print "dut_index", dut_index
                    dut_name = dut_names[actual_dut] if dut_names else ("DUT " + str(actual_dut))
                    logging.info('Calculate efficiency for DUT %d', actual_dut)
    
                    # Calculate histogram properties (bins size and number of bins)
                    bin_size = [bin_size, ] if not isinstance(bin_size, Iterable) else bin_size
                    if len(bin_size) != 1:
                        actual_bin_size_x = bin_size[dut_index][0]
                        actual_bin_size_y = bin_size[dut_index][1]
                    else:
                        actual_bin_size_x = bin_size[0][0]
                        actual_bin_size_y = bin_size[0][1]
    
                    if len(sensor_sizes) == 1:
                        sensor_size = sensor_sizes[0]
                    else:
                        sensor_size = sensor_sizes[actual_dut]
                    if sensor_size is None:
                        sensor_size = np.array(pixel_size[actual_dut]) * n_pixels[actual_dut]
                    print "sensor_size", sensor_size

                    extend_bins = 0
                    sensor_range_corr = [[-0.5 * pixel_size[actual_dut][0] * n_pixels[actual_dut][0] - extend_bins * actual_bin_size_x, 0.5 * pixel_size[actual_dut][0] * n_pixels[actual_dut][0] + extend_bins * actual_bin_size_x], [- 0.5 * pixel_size[actual_dut][1] * n_pixels[actual_dut][1] - extend_bins * actual_bin_size_y, 0.5 * pixel_size[actual_dut][1] * n_pixels[actual_dut][1] + extend_bins * actual_bin_size_y]]
                    print "sensor_range_corr", sensor_range_corr
    
                    sensor_range_corr_with_distance = sensor_range_corr[:]
                    sensor_range_corr_with_distance.append([0, max_distance[dut_index]])
    
                    sensor_range_corr_with_charge = sensor_range_corr[:]
                    sensor_range_corr_with_charge.append([0, charge_bins[dut_index]])
    
                    print sensor_size[0], actual_bin_size_x, sensor_size[1], actual_bin_size_y
                    n_bin_x = sensor_size[0] / actual_bin_size_x
                    n_bin_y = sensor_size[1] / actual_bin_size_y
                    if not n_bin_x.is_integer() or not n_bin_y.is_integer():
                        raise ValueError("change bin_size: %f, %f" % (n_bin_x, n_bin_x))
                    n_bin_x = int(n_bin_x)
                    n_bin_y = int(n_bin_y)
                    # has to be even
                    print "bins", n_bin_x, n_bin_y
    
    
                    # Define result histograms, these are filled for each hit chunk
    #                 total_distance_array = np.zeros(shape=(n_bin_x, n_bin_y, max_distance))
                    total_hit_hist = None
                    total_track_density = None
                    total_track_density_with_dut_hit = None
                    distance_array = None
                    hit_hist = None
                    charge_array = None
                    average_charge_valid_hit = None
    
                    for tracks_chunk, _ in analysis_utils.data_aligned_at_events(node, chunk_size=chunk_size):
                        # Cut in Chi 2 of the track fit
                        if max_chi2[dut_index]:
                            tracks_chunk = tracks_chunk[tracks_chunk['track_chi2'] <= max_chi2[dut_index]]
    
                        # Transform the hits and track intersections into the local coordinate system
                        # Coordinates in global coordinate system (x, y, z)
                        hit_x, hit_y, hit_z = tracks_chunk['x_dut_%d' % actual_dut], tracks_chunk['y_dut_%d' % actual_dut], tracks_chunk['z_dut_%d' % actual_dut]
                        charge = tracks_chunk['charge_dut_%d' % actual_dut]
                        # track intersection at DUT
                        
                        intersection_x, intersection_y, intersection_z = tracks_chunk['offset_0'], tracks_chunk['offset_1'], tracks_chunk['offset_2']

                        # Transform to local coordinate system
                        if use_prealignment:
                            hit_x_local, hit_y_local, hit_z_local = geometry_utils.apply_alignment(hit_x, hit_y, hit_z,
                                                                                                   dut_index=actual_dut,
                                                                                                   prealignment=prealignment,
                                                                                                   inverse=True)
                            intersection_x_local, intersection_y_local, intersection_z_local = geometry_utils.apply_alignment(intersection_x, intersection_y, intersection_z,
                                                                                                                              dut_index=actual_dut,
                                                                                                                              prealignment=prealignment,
                                                                                                                              inverse=True)
                        else:  # Apply transformation from fine alignment information
                            hit_x_local, hit_y_local, hit_z_local = geometry_utils.apply_alignment(hit_x, hit_y, hit_z,
                                                                                                   dut_index=actual_dut,
                                                                                                   alignment=alignment,
                                                                                                   inverse=True)
                            intersection_x_local, intersection_y_local, intersection_z_local = geometry_utils.apply_alignment(intersection_x, intersection_y, intersection_z,
                                                                                                                              dut_index=actual_dut,
                                                                                                                              alignment=alignment,
                                                                                                                              inverse=True)
    
                        intersections_local = np.column_stack((intersection_x_local, intersection_y_local, intersection_z_local))
                        hits_local = np.column_stack((hit_x_local, hit_y_local, hit_z_local))
    
                        # Select valid hits/tracks
                        selection = np.logical_and(~np.isnan(hit_x), ~np.isnan(hit_y))
    
                        if not np.allclose(hit_z_local[selection], 0.0) or not np.allclose(intersection_z_local[selection], 0.0):
                            raise RuntimeError('The transformation to the local coordinate system did not give all z = 0. Wrong alignment used?')
    
                        # Usefull for debugging, print some inefficient events that can be cross checked
                        if show_inefficient_events:
                            logging.info('These events are inefficient: %s', str(tracks_chunk['event_number'][selection]))
    
                        # Select hits from column, row range (e.g. to supress edge pixels)
                        # TODO: commented because not needed
    #                     col_range = [col_range, ] if not isinstance(col_range, Iterable) else col_range
    #                     row_range = [row_range, ] if not isinstance(row_range, Iterable) else row_range
    #                     if len(col_range) == 1:
    #                         index = 0
    #                     if len(row_range) == 1:
    #                         index = 0
    #                     if col_range[index] is not None:
    #                         selection = np.logical_and(intersections_local[:, 0] >= col_range[index][0], intersections_local[:, 0] <= col_range[index][1])  # Select real hits
    #                         hits_local, intersections_local = hits_local[selection], intersections_local[selection]
    #                     if row_range[index] is not None:
    #                         selection = np.logical_and(intersections_local[:, 1] >= row_range[index][0], intersections_local[:, 1] <= row_range[index][1])  # Select real hits
    #                         hits_local, intersections_local = hits_local[selection], intersections_local[selection]
    
                        # Calculate distance between track hit and DUT hit
                        # TODO: scale correct? USE np.square(np.array((1, 1, 1)))
                        scale = np.square(np.array((1, 1, 1)))  # Regard pixel size for calculating distances
                        distance = np.sqrt(np.dot(np.square(intersections_local - hits_local), scale))  # Array with distances between DUT hit and track hit for each event. Values in um
    
                        total_hit_hist_tmp = np.histogram2d(hits_local[:, 0], hits_local[:, 1], bins=(n_bin_x, n_bin_y), range=sensor_range_corr)[0]
                        if total_hit_hist is None:
                            total_hit_hist = total_hit_hist_tmp
                        else:
                            total_hit_hist += total_hit_hist_tmp
    
                        total_track_density_tmp = np.histogram2d(intersections_local[:, 0], intersections_local[:, 1], bins=(n_bin_x, n_bin_y), range=sensor_range_corr)[0]
                        if total_track_density is None:
                            total_track_density = total_track_density_tmp
                        else:
                            total_track_density += total_track_density_tmp
    
                        # Calculate efficiency
                        if cut_distance[dut_index] is not None:  # Select intersections where hit is in given distance around track intersection
                            selection = np.logical_and(selection, distance < cut_distance[dut_index])
                        intersections_local_valid_hit = intersections_local[selection]
                        hits_local_valid_hit = hits_local[selection]
                        charge_valid_hit = charge[selection]
    
                        total_track_density_with_dut_hit_tmp, xedges, yedges = np.histogram2d(intersections_local_valid_hit[:, 0], intersections_local_valid_hit[:, 1], bins=(n_bin_x, n_bin_y), range=sensor_range_corr)
                        if total_track_density_with_dut_hit is None:
                            total_track_density_with_dut_hit = total_track_density_with_dut_hit_tmp
                        else:
                            total_track_density_with_dut_hit += total_track_density_with_dut_hit_tmp
    
                        intersections_distance = np.column_stack((intersections_local[:, 0], intersections_local[:, 1], distance))
    
                        distance_array_tmp = np.histogramdd(intersections_distance, bins=(n_bin_x, n_bin_y, 100), range=sensor_range_corr_with_distance)[0]
                        if distance_array is None:
                            distance_array = distance_array_tmp
                        else:
                            distance_array += distance_array_tmp
    
                        hit_hist_tmp = np.histogram2d(hits_local[:, 0], hits_local[:, 1], bins=(n_bin_x, n_bin_y), range=sensor_range_corr)[0]
                        if hit_hist is None:
                            hit_hist = hit_hist_tmp
                        else:
                            hit_hist += hit_hist_tmp
    
                        if charge_bins[dut_index] is not None:
                            average_charge_valid_hit_tmp, _, _, _ = binned_statistic_2d(intersections_local_valid_hit[:, 0], intersections_local_valid_hit[:, 1], charge_valid_hit[:], statistic="mean", bins=(n_bin_x, n_bin_y), range=sensor_range_corr)
                            average_charge_valid_hit_tmp = np.nan_to_num(average_charge_valid_hit_tmp)
                            if average_charge_valid_hit is None:
                                average_charge_valid_hit = average_charge_valid_hit_tmp
                            else:
                                average_charge_valid_hit[total_track_density_with_dut_hit != 0] = (((average_charge_valid_hit * total_track_density_with_dut_hit_previous) + (average_charge_valid_hit_tmp * total_track_density_with_dut_hit_tmp)) / total_track_density_with_dut_hit)[total_track_density_with_dut_hit != 0]
                            total_track_density_with_dut_hit_previous = total_track_density_with_dut_hit.copy()

                            intersection_charge_valid_hit = np.column_stack((intersections_local_valid_hit[:, 0], intersections_local_valid_hit[:, 1], charge_valid_hit[:]))
                            charge_array_tmp = np.histogramdd(intersection_charge_valid_hit, bins=(n_bin_x, n_bin_y, charge_bins[dut_index]), range=sensor_range_corr_with_charge)[0]
                            if charge_array is None:
                                charge_array = charge_array_tmp
                            else:
                                charge_array += charge_array_tmp
    
                        if np.all(total_track_density == 0):
                            logging.warning('No tracks on DUT %d, cannot calculate efficiency', actual_dut)
                            continue
    
                    if charge_bins[dut_index] is not None:
                        average_charge_valid_hit = np.ma.masked_where(total_track_density_with_dut_hit == 0, average_charge_valid_hit)
                    # efficiency
                    efficiency = np.full_like(total_track_density_with_dut_hit, fill_value=np.nan, dtype=np.float)
                    efficiency[total_track_density != 0] = total_track_density_with_dut_hit[total_track_density != 0].astype(np.float) / total_track_density[total_track_density != 0].astype(np.float) * 100.0
                    efficiency = np.ma.masked_invalid(efficiency)
                    efficiency = np.ma.masked_where(total_track_density < minimum_tracks_per_bin, efficiency)
    
                    distance_mean_array = np.average(distance_array, axis=2, weights=range(0, 100)) * sum(range(0, 100)) / np.sum(distance_array, axis=2)
    
                    distance_mean_array = np.ma.masked_invalid(distance_mean_array)
#                     distance_max_array = np.amax(distance_array, axis=2) * sum(range(0, max_distance)) / hit_hist.astype(np.float)
#                     distance_min_array = np.amin(distance_array, axis=2) * sum(range(0, max_distance)) / hit_hist.astype(np.float)
#                     distance_max_array = np.ma.masked_invalid(distance_max_array)
#                     distance_min_array = np.ma.masked_invalid(distance_min_array)
    
                    print "bins with tracks", np.ma.count(efficiency), "of", efficiency.shape[0] * efficiency.shape[1]
                    print "tracks outside left / right", np.where(intersections_local_valid_hit[:, 0] < sensor_range_corr[0][0])[0].shape[0], np.where(intersections_local_valid_hit[:, 0] > sensor_range_corr[0][1])[0].shape[0]
                    print "tracks outside below / above", np.where(intersections_local_valid_hit[:, 1] < sensor_range_corr[1][0])[0].shape[0], np.where(intersections_local_valid_hit[:, 1] > sensor_range_corr[1][1])[0].shape[0]
    
                    if pixel_size:
                        aspect = pixel_size[actual_dut][1] / pixel_size[actual_dut][0]
                    else:
                        aspect = "auto"
    
                    plot_utils.efficiency_plots(
                        distance_mean_array=distance_mean_array,
                        hit_hist=hit_hist,
                        track_density=total_track_density,
                        track_density_with_hit=total_track_density_with_dut_hit,
                        efficiency=efficiency,
                        charge_array=charge_array,
                        average_charge=average_charge_valid_hit,
                        dut_name=dut_name,
                        plot_range=sensor_range_corr,
                        efficiency_range=efficiency_range[dut_index],
                        bin_size=bin_size[dut_index],
                        xedges=xedges,
                        yedges=yedges,
                        n_pixels=n_pixels[actual_dut],
                        charge_bins=charge_bins[dut_index],
                        dut_mask=dut_masks[dut_index],
                        output_fig=output_fig,
                        aspect=aspect)
                    logging.info('Efficiency =  %.4f', np.ma.mean(efficiency))
    
                    actual_dut_folder = out_file_h5.create_group(out_file_h5.root, 'DUT_%d' % actual_dut)
                    out_efficiency = out_file_h5.createCArray(actual_dut_folder, name='Efficiency', title='Efficiency per bin of DUT%d' % actual_dut, atom=tb.Atom.from_dtype(efficiency.dtype), shape=efficiency.T.shape, filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                    out_tracks_per_bin = out_file_h5.createCArray(actual_dut_folder, name='Tracks_per_bin', title='Tracks per bin of DUT%d' % actual_dut, atom=tb.Atom.from_dtype(total_track_density.dtype), shape=total_track_density.T.shape, filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                    # Store parameters used for efficiency calculation
                    # TODO: add attributes to DUT group
                    # TODO: adding all attributes and histograms
                    out_efficiency.attrs.bin_size = bin_size
                    out_efficiency.attrs.minimum_tracks_per_bin = minimum_tracks_per_bin
                    out_efficiency.attrs.sensor_size = sensor_size
                    out_efficiency.attrs.use_duts = use_duts
                    out_efficiency.attrs.max_chi2 = max_chi2
                    out_efficiency.attrs.cut_distance = cut_distance[dut_index]
                    out_efficiency.attrs.max_distance = max_distance[dut_index]
                    out_efficiency.attrs.charge_bins = charge_bins[dut_index]
    #                     out_efficiency.attrs.col_range = col_range
    #                     out_efficiency.attrs.row_range = row_range
                    out_efficiency[:] = efficiency.T
                    out_tracks_per_bin[:] = total_track_density.T
    
                    efficiencies.append(np.ma.mean(efficiency))
                    pass_tracks.append(total_track_density_with_dut_hit.sum())
                    total_tracks.append(total_track_density.sum())

    return efficiencies, pass_tracks, total_tracks
