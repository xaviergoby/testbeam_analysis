''' All functions creating results (e.g. efficiency, residuals, track density) from fitted tracks are listed here.'''
from __future__ import division

import logging
import re
from collections import Iterable

import tables as tb
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from scipy.optimize import curve_fit

from testbeam_analysis import plot_utils
from testbeam_analysis import geometry_utils


def gauss(x, *p):
    A, mu, sigma = p
    return A * np.exp(-(x - mu) ** 2 / (2. * sigma ** 2))

# FIXME: calculate_residuals should not care how the tracks were fitted; thus this function is not needed


def calculate_residuals_kalman(input_tracks_file, z_positions, use_duts=None, max_chi2=None, output_pdf=None, method="Interpolation", geometryFile=None):
    '''Takes the tracks and calculates residuals for selected DUTs in col, row direction.
    Parameters
    ----------
    input_tracks_file : string
        File name with the tracks table
    z_position : iterable
        The positions of the devices in z in cm
    use_duts : iterable
        The duts to calculate residuals for. If None all duts in the input_tracks_file are used
    max_chi2 : int
        Use only converged fits (cut on chi2)
    output_pdf : pdf file name
        If None plots are printed to screen.
        If False no plots are created.
    Returns
    -------
    A list of residuals in column row. e.g.: [Col residual DUT 0, Row residual DUT 0, Col residual DUT 1, Row residual DUT 1, ...]
    '''
    logging.info('=== Calculate residuals ===')

    def gauss(x, *p):
        A, mu, sigma = p
        return A * np.exp(-(x - mu) ** 2 / (2. * sigma ** 2))

    output_fig = PdfPages(output_pdf) if output_pdf else None

    residuals = []

    with tb.open_file(input_tracks_file, mode='r') as in_file_h5:
        translations, rotations = geometry_utils.recontruct_geometry_from_file(geometryFile)
        for node in in_file_h5.root:
            actual_dut = int(re.findall(r'\d+', node.name)[-1])
            if use_duts and actual_dut not in use_duts:
                continue
            logging.info('Calculate residuals for DUT %d', actual_dut)

            track_array = node[:]

            if max_chi2:
                track_array = track_array[track_array['track_chi2'] <= max_chi2]
            track_array = track_array[np.logical_and(track_array['column_dut_%d' % actual_dut] != 0., track_array['row_dut_%d' % actual_dut] != 0.)]  # take only tracks where actual dut has a hit, otherwise residual wrong
            if method == "Interpolation2":
                hits, offset, slope = np.column_stack((track_array['column_dut_%d' % actual_dut], track_array['row_dut_%d' % actual_dut], np.repeat(z_positions[actual_dut], track_array.shape[0]))), np.column_stack((track_array['offset_0'], track_array['offset_1'], track_array['offset_2'])), np.column_stack((track_array['slope_0'], track_array['slope_1'], track_array['slope_2']))
                intersection = offset + slope / slope[:, 2, np.newaxis] * (z_positions[actual_dut] - offset[:, 2, np.newaxis])  # intersection track with DUT plane
            elif method == "Kalman" or method == "Interpolation":
                hits, intersection = np.column_stack((track_array['column_dut_%d' % actual_dut], track_array['row_dut_%d' % actual_dut], np.repeat(z_positions[actual_dut], track_array.shape[0]))), np.column_stack((track_array['predicted_x%d' % actual_dut], track_array['predicted_y%d' % actual_dut], np.repeat(z_positions[actual_dut], track_array.shape[0])))

            tmpc = hits[:, 0] * rotations[actual_dut, 0, 0] + hits[:, 1] * rotations[actual_dut, 0, 1] + translations[actual_dut, 0]
            tmpr = hits[:, 0] * rotations[actual_dut, 1, 0] + hits[:, 1] * rotations[actual_dut, 1, 1] + translations[actual_dut, 1]
            hits[:, 0] = tmpc
            hits[:, 1] = tmpr

            difference = hits - intersection

            for i in range(2):  # col / row
                mean, rms = np.mean(difference[:, i]), np.std(difference[:, i])
                hist, edges = np.histogram(difference[:, i], range=(mean - 5. * rms, mean + 5. * rms), bins=1000)
                fit_ok = False
                try:
                    coeff, var_matrix = curve_fit(gauss, edges[:-1], hist, p0=[np.amax(hist), mean, rms])
                    fit_ok = True
                except:
                    fit_ok = False

                if output_pdf is not False:
                    plot_utils.plot_residuals(i, actual_dut, edges, hist, fit_ok, coeff, gauss, difference, var_matrix, output_fig=output_fig)
                residuals.append(np.abs(coeff[2]))

                for j in range(2):
                    _, xedges, yedges = np.histogram2d(hits[:, i], difference[:, j], bins=[100, 100], range=[[np.amin(hits[:, i]), np.amax(hits[:, i])], [-100, 100]])
                    plot_utils.plot_residuals_correlations(i, j, actual_dut, xedges, yedges, hits[:, i], difference[:, j], output_fig)
#                    s = analysis_utils.hist_2d_index(hits[:,i], difference[:,j], shape=(50,50))
                    # if j != i:
                    mean_fitted, selected_data, fit, pcov = calculate_correlation_fromplot(hits[:, i], difference[:, j], xedges, yedges, dofit=True)
                    plot_utils.plot_residuals_correlations_fit(i, j, actual_dut, xedges, yedges, mean_fitted, selected_data, fit, pcov, output_fig)

    if output_fig:
        output_fig.close()

    return residuals


def calculate_residuals(input_tracks_file, use_duts=None, max_chi2=None, output_pdf=None):
    '''Takes the tracks and calculates residuals for selected DUTs in col, row direction.
    Parameters
    ----------
    input_tracks_file : string
        File name with the tracks table
    use_duts : iterable
        The duts to calculate residuals for. If None all duts in the input_tracks_file are used
    max_chi2 : int
        Use only converged fits (cut on chi2)
    output_pdf : pdf file name
        If None plots are printed to screen.
        If False no plots are created.
    Returns
    -------
    A list of residuals in column row. e.g.: [Col residual DUT 0, Row residual DUT 0, Col residual DUT 1, Row residual DUT 1, ...]
    '''
    logging.info('=== Calculate residuals ===')

    def gauss(x, *p):
        A, mu, sigma = p
        return A * np.exp(-(x - mu) ** 2 / (2. * sigma ** 2))

    output_fig = PdfPages(output_pdf) if output_pdf else None

    residuals = []

    with tb.open_file(input_tracks_file, mode='r') as in_file_h5:
        for node in in_file_h5.root:
            actual_dut = int(re.findall(r'\d+', node.name)[-1])
            if use_duts and actual_dut not in use_duts:
                continue
            logging.info('Calculate residuals for DUT %d', actual_dut)

            # FIXME: has to be chunked
            track_array = node[:]

            if max_chi2:
                track_array = track_array[track_array['track_chi2'] <= max_chi2]
            track_array = track_array[np.logical_and(track_array['x_dut_%d' % actual_dut] != 0., track_array['y_dut_%d' % actual_dut] != 0.)]  # take only tracks where actual dut has a hit, otherwise residual wrong
            hits = np.column_stack((track_array['x_dut_%d' % actual_dut], track_array['y_dut_%d' % actual_dut], track_array['z_dut_%d' % actual_dut]))
            intersection = np.column_stack((track_array['offset_0'], track_array['offset_1'], track_array['offset_2']))  # Intersection of track with DUT plane is the offset of the track definition (by convention)
            difference = intersection - hits

            for i in range(2):  # col / row
                mean, rms = np.mean(difference[:, i]), np.std(difference[:, i])
                hist, edges = np.histogram(difference[:, i], range=(mean - 5. * rms, mean + 5. * rms), bins=1000)
                fit_ok = False
                coeff, var_matrix = None, None
                try:
                    coeff, var_matrix = curve_fit(gauss, edges[:-1], hist, p0=[np.amax(hist), mean, rms])
                    fit_ok = True
                    residuals.append(np.abs(coeff[2]))
                except:
                    fit_ok = False
                    residuals.append(-1)

                if output_pdf is not False:
                    plot_utils.plot_residuals(i, actual_dut, edges, hist, fit_ok, coeff, gauss, difference, var_matrix, output_fig=output_fig)

    if output_fig:
        output_fig.close()

    return residuals


def calculate_correlation_fromplot(data1, data2, edges1, edges2, dofit=True):
    step = edges1[1] - edges1[0]
    nbins = len(edges1)
    resx = [[]] * len(edges1)
    mean_fitted = np.zeros(nbins)
    mean_error_fitted = np.zeros(nbins)

    for i, x in enumerate(data1):
        n = np.int((x - edges1[0]) / step)
        resx[n].append(data2[i])

    for n in range(nbins):
        if len(resx[n]) == 0:
            mean_fitted[n] = -1
            continue
        p0 = [np.amax(resx[n]), 0, 10]
        hist, edges = np.histogram(resx[n], range=(edges2[0], edges2[-1]), bins=len(edges2))
        ed = (edges[:-1] + edges[1:]) / 2.
        try:
            coeff, var_matrix = curve_fit(gauss, ed, hist, p0=p0)
            if var_matrix[1, 1] > 0.1:
                ''' > 0.01 for kalman'''
                ''' TOFIX: cut must be parameter!'''
                mean_fitted[n] = -1
                continue
            mean_fitted[n] = coeff[1]
            mean_error_fitted[n] = np.sqrt(np.abs(np.diag(var_matrix)))[1]
            # sigma_fitted[index] = coeff[2]
        except RuntimeError:
            pass

    mean_fitted[~np.isfinite(mean_fitted)] = -1
    selected_data = np.where(np.logical_and(mean_fitted != -1, 1 > 0))[0]

    f = lambda x, c0, c1: c0 + c1 * x
    if dofit:
        fit, pcov = curve_fit(f, edges1[selected_data], mean_fitted[selected_data])
    else:
        fit, pcov = None, None

    print("Linear fit:")
    print(fit)
    print(pcov)

    return mean_fitted, selected_data, fit, pcov


def calculate_efficiency(input_tracks_file, output_pdf, bin_size, minimum_track_density, sensor_size=None, use_duts=None, max_chi2=None, cut_distance=500, max_distance=500, col_range=None, row_range=None, output_file=None):
    '''Takes the tracks and calculates the hit efficiency and hit/track hit distance for selected DUTs.
    Parameters
    ----------
    input_tracks_file : string
        file name with the tracks table
    output_pdf : pdf file name object
    bin_size : iterable
        sizes of bins (i.e. (virtual) pixel size). Give one tuple (x, y) for every plane or list of tuples for different planes
    minimum_track_density : int
        minimum track density required to consider bin for efficiency calculation
    sensor_size : iterable
        size of the used sensor in um. Give one tuple (x, y) for every plane or list of tuples for different planes
    use_duts : iterable
        the DUTs to calculate efficiency for. If None all duts are used
    max_chi2 : int
        only use track with a chi2 <= max_chi2
    cut_distance : int
        use only distances (between DUT hit and track hit) smaller than cut_distance
    max_distance : int
        defines binnig of distance values
    col_range, row_range : iterable
        column / row value to calculate efficiency for (to neglect noisy edge pixels for efficiency calculation)
    '''
    logging.info('=== Calculate efficiency ===')

    with PdfPages(output_pdf) as output_fig:
        efficiencies = []
        with tb.open_file(input_tracks_file, mode='r') as in_file_h5:
            for index, node in enumerate(in_file_h5.root):
                actual_dut = int(re.findall(r'\d+', node.name)[-1])
                if use_duts and actual_dut not in use_duts:
                    continue
                logging.info('Calculate efficiency for DUT %d', actual_dut)
                track_array = node[:]

                # Get pixel and bin sizes for calculations and plotting
                # Allow different sensor sizes for every plane
                if not sensor_size:
                    dimensions = (np.amax(track_array['x_dut_%d' % actual_dut]), np.amax(track_array['y_dut_%d' % actual_dut]))
                else:
                    dimensions = [sensor_size, ] if not isinstance(sensor_size, list) else sensor_size
                    if len(dimensions) == 1:
                        dimensions = dimensions[0]
                    else:
                        dimensions = dimensions[index]

                # Allow different bin_sizes for every plane
                bin_size = [bin_size, ] if not isinstance(bin_size, list) else bin_size
                if len(bin_size) != 1:
                    actual_bin_size_x = bin_size[index][0]
                    actual_bin_size_y = bin_size[index][1]
                else:
                    actual_bin_size_x = bin_size[0][0]
                    actual_bin_size_y = bin_size[0][1]

                n_bin_x = dimensions[0] / actual_bin_size_x
                n_bin_y = dimensions[1] / actual_bin_size_y

                # Cut in Chi 2 of the track fit
                if max_chi2:
                    track_array = track_array[track_array['track_chi2'] <= max_chi2]

                # Take hits of actual DUT and track projection on actual DUT plane
                hits = np.column_stack((track_array['x_dut_%d' % actual_dut], track_array['y_dut_%d' % actual_dut], track_array['z_dut_%d' % actual_dut]))
                intersection = np.column_stack((track_array['offset_0'], track_array['offset_1'], track_array['offset_2']))  # Intersection of track with DUT plane is the offset of the track definition (by convention)

                # Select hits from column row range (e.g. to supress edge pixels)
                col_range = [col_range, ] if not isinstance(col_range, list) else col_range
                row_range = [row_range, ] if not isinstance(row_range, list) else row_range
                if len(col_range) == 1:
                    index = 0
                if len(row_range) == 1:
                    index = 0
                if col_range[index] is not None:
                    selection = np.logical_and(intersection[:, 0] >= col_range[index][0], intersection[:, 0] <= col_range[index][1])
                    hits, intersection = hits[selection], intersection[selection]
                if row_range[index] is not None:
                    selection = np.logical_and(intersection[:, 1] >= row_range[index][0], intersection[:, 1] <= row_range[index][1])
                    hits, intersection = hits[selection], intersection[selection]

                # Calculate distance between track hit and DUT hit
                scale = np.square(np.array((1, 1, 0)))  # regard pixel size for calculating distances
                distance = np.sqrt(np.dot(np.square(intersection - hits), scale))  # array with distances between DUT hit and track hit for each event. Values in um

                col_row_distance = np.column_stack((hits[:, 0], hits[:, 1], distance))
                distance_array = np.histogramdd(col_row_distance, bins=(n_bin_x, n_bin_y, max_distance), range=[[0, dimensions[0]], [0, dimensions[1]], [0, max_distance]])[0]
                hit_hist, _, _ = np.histogram2d(hits[:, 0], hits[:, 1], bins=(n_bin_x, n_bin_y), range=[[0, dimensions[0]], [0, dimensions[1]]])

                # Calculate distances between hit and intersection
                distance_mean_array = np.average(distance_array, axis=2, weights=range(0, max_distance)) * sum(range(0, max_distance)) / hit_hist.astype(np.float)
                distance_mean_array = np.ma.masked_invalid(distance_mean_array)
                distance_max_array = np.amax(distance_array, axis=2) * sum(range(0, max_distance)) / hit_hist.astype(np.float)
                distance_min_array = np.amin(distance_array, axis=2) * sum(range(0, max_distance)) / hit_hist.astype(np.float)
                distance_max_array = np.ma.masked_invalid(distance_max_array)
                distance_min_array = np.ma.masked_invalid(distance_min_array)

                # Calculate efficiency
                if cut_distance:  # Select intersections where hit is in given distance around track intersection
                    intersection_valid_hit = intersection[np.logical_and(np.logical_and(hits[:, 0] != 0, hits[:, 1] != 0), distance < cut_distance)]
                else:
                    intersection_valid_hit = intersection[np.logical_and(hits[:, 0] != 0, hits[:, 1] != 0)]

                track_density, _, _ = np.histogram2d(intersection[:, 0], intersection[:, 1], bins=(n_bin_x, n_bin_y), range=[[0, dimensions[0]], [0, dimensions[1]]])
                track_density_with_DUT_hit, _, _ = np.histogram2d(intersection_valid_hit[:, 0], intersection_valid_hit[:, 1], bins=(n_bin_x, n_bin_y), range=[[0, dimensions[0]], [0, dimensions[1]]])
                efficiency = np.zeros_like(track_density_with_DUT_hit)
                efficiency[track_density != 0] = track_density_with_DUT_hit[track_density != 0].astype(np.float) / track_density[track_density != 0].astype(np.float) * 100.
                efficiency = np.ma.array(efficiency, mask=track_density < minimum_track_density)

                plot_utils.efficiency_plots(distance_min_array, distance_max_array, distance_mean_array, hit_hist, track_density, track_density_with_DUT_hit, efficiency, actual_dut, minimum_track_density, plot_range=dimensions, cut_distance=cut_distance, output_fig=output_fig)

                logging.info('Efficiency =  %1.4f +- %1.4f', np.ma.mean(efficiency), np.ma.std(efficiency))
                efficiencies.append(np.ma.mean(efficiency))

                if output_file:
                    with tb.open_file(output_file, 'a') as out_file_h5:
                        actual_dut_folder = out_file_h5.create_group(out_file_h5.root, 'DUT_%d' % actual_dut)
                        out_efficiency = out_file_h5.createCArray(actual_dut_folder, name='Efficiency', title='Efficiency map of DUT%d' % actual_dut, atom=tb.Atom.from_dtype(efficiency.dtype), shape=efficiency.T.shape, filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                        out_efficiency_mask = out_file_h5.createCArray(actual_dut_folder, name='Efficiency_mask', title='Masked pixel map of DUT%d' % actual_dut, atom=tb.Atom.from_dtype(efficiency.mask.dtype), shape=efficiency.mask.T.shape, filters=tb.Filters(complib='blosc', complevel=5, fletcher32=False))
                        # Store parameters used for efficiency calculation
                        out_efficiency.attrs.bin_size = bin_size
                        out_efficiency.attrs.minimum_track_density = minimum_track_density
                        out_efficiency.attrs.sensor_size = sensor_size
                        out_efficiency.attrs.use_duts = use_duts
                        out_efficiency.attrs.max_chi2 = max_chi2
                        out_efficiency.attrs.cut_distance = cut_distance
                        out_efficiency.attrs.max_distance = max_distance
                        out_efficiency.attrs.col_range = col_range
                        out_efficiency.attrs.row_range = row_range
                        out_efficiency[:] = efficiency.T
                        out_efficiency_mask[:] = efficiency.mask.T
    return efficiencies
