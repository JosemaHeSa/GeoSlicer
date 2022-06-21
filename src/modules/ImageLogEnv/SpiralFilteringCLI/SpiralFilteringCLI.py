#!/usr/bin/env python-real
# -*- coding: utf-8 -*-

# IMPORTANT never forget to start your CLI with those lines above

from __future__ import print_function

# These imports should go first to guarantee the transversing of wrapped classes by instantiation time
# Refer to github.com/Slicer/Slicer/issues/6484
import vtk, slicer, slicer.util, mrml

import logging
import os, sys
import traceback

from xarray import where

from ltrace.slicer.helpers import getVolumeNullValue


import json

from pathlib import Path
import numpy as np
import csv

from scipy.optimize import minimize
from scipy.stats import kurtosis
from scipy.stats import skew
from scipy.special import comb
from scipy.signal import convolve2d
from ltrace.image.optimized_transforms import DEFAULT_NULL_VALUE as DEFAULT_NULL_VALUES

DEFAULT_NULL_VALUE = list(DEFAULT_NULL_VALUES)[0]


def progressUpdate(value):
    """
    Progress Bar updates over stdout (Slicer handles the parsing)
    """
    print(f"<filter-progress>{value}</filter-progress>")
    sys.stdout.flush()


def readFrom(volumeFile, builder):
    sn = slicer.vtkMRMLNRRDStorageNode()
    sn.SetFileName(volumeFile)
    nodeIn = builder()
    sn.ReadData(nodeIn)  # read data from volumeFile into nodeIn
    return nodeIn


def writeDataInto(volumeFile, dataVoxelArray, builder, reference=None):
    sn_out = slicer.vtkMRMLNRRDStorageNode()
    sn_out.SetFileName(volumeFile)
    nodeOut = builder()

    if reference:
        # copy image information
        nodeOut.Copy(reference)
        # reset the attribute dictionary, otherwise it will be transferred over
        attrs = vtk.vtkStringArray()
        nodeOut.GetAttributeNames(attrs)
        for i in range(0, attrs.GetNumberOfValues()):
            nodeOut.SetAttribute(attrs.GetValue(i), None)

    # reset the data array to force resizing, otherwise we will just keep the old data too
    nodeOut.SetAndObserveImageData(None)
    slicer.util.updateVolumeFromArray(nodeOut, dataVoxelArray)
    nodeOut.Modified()

    sn_out.WriteData(nodeOut)


def filter_spiral(data, T_depth, wlength_min=3.0, wlength_max=100.0, factor=1.0, transit_bandw=0.02):

    I = np.shape(data)[0]

    media = data.mean()
    data = data - media

    Fnorm_transit_bandw = 2 * T_depth * transit_bandw / 2
    Fnorm_transit_bandw_index = round(Fnorm_transit_bandw * I)
    Fnorm_max = 2 * T_depth / wlength_min
    Fnorm_max_index = round(Fnorm_max * I)
    Fnorm_min = 2 * T_depth / wlength_max
    Fnorm_min_index = round(Fnorm_min * I)

    FFT_abs = np.abs(np.fft.fft2(data))
    FFT_angle = np.angle(np.fft.fft2(data))

    ## Filtering step ##
    espiral_abs = np.zeros(np.shape(FFT_abs))

    # filtering in the positive side of the spectrum
    filtro1 = smoothstep(
        np.arange(I), Fnorm_min_index - Fnorm_transit_bandw_index, Fnorm_min_index + Fnorm_transit_bandw_index, 1
    )
    filtro2 = 1 - smoothstep(
        np.arange(I), Fnorm_max_index - Fnorm_transit_bandw_index, Fnorm_max_index + Fnorm_transit_bandw_index, 1
    )
    filtro = filtro1 * filtro2

    espiral_abs[:, 1] = FFT_abs[:, 1] * filtro * factor

    # filtering in the negative side of the spectrum
    filtro1 = np.flip(filtro1)
    filtro1 = np.concatenate((np.array([filtro1[0]]), filtro1[0:-1]))
    filtro2 = np.flip(filtro2)
    filtro2 = np.concatenate((np.array([filtro2[0]]), filtro2[0:-1]))
    filtro = filtro1 * filtro2

    espiral_abs[:, -1] = FFT_abs[:, -1] * filtro * factor

    # IFFT and return ##
    espiral_fft = espiral_abs * np.cos(FFT_angle) + espiral_abs * np.sin(FFT_angle) * 1j
    espiral = np.real(np.fft.ifft2(espiral_fft))

    data_filtered = data - espiral + media

    data_noise = espiral

    return data_filtered, data_noise


def smoothstep(x, x_min=0, x_max=1, N=1):
    # from https://stackoverflow.com/questions/45165452/how-to-implement-a-smooth-clamp-function-in-python
    x = np.clip((x - x_min) / (x_max - x_min), 0, 1)

    result = 0
    for n in range(0, N + 1):
        result += comb(N + n, n) * comb(2 * N + 1, N - n) * (-x) ** n

    result *= x ** (N + 1)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LTrace Image Compute Wrapper for Slicer.")
    parser.add_argument("--master1", type=str, dest="inputVolume1", required=True, help="Amplitude image log")
    parser.add_argument("--wlength_min", type=float, default=1, help="Multiplier value")
    parser.add_argument("--wlength_max", type=float, default=1, help="Multiplier value")
    parser.add_argument(
        "--outputvolume_std", type=str, dest="outputVolume_std", default=None, help="Output labelmap (3d) Values"
    )
    parser.add_argument("--nullable", type=float, default=DEFAULT_NULL_VALUE, help="Null value representation")
    parser.add_argument("--multip_factor", type=float, default=-1000000.0, help="Minimum amplitude value")
    parser.add_argument("--smoothstep_factor", type=float, default=-1000000.0, help="Minimum amplitude value")

    # This argument is automatically provided by Slicer channels, just capture it when using argparse
    parser.add_argument("--returnparameterfile", type=str, help="File destination to store an execution outputs")

    args = parser.parse_args()

    progressUpdate(value=0.1)

    # Read as slicer node (copy)
    amplitude = readFrom(args.inputVolume1, mrml.vtkMRMLScalarVolumeNode)
    T_depth = amplitude.GetSpacing()[-1] / 1000.0
    amplitudeNodeNullValue = args.nullable

    amplitude_array = slicer.util.arrayFromVolume(amplitude)
    if issubclass(amplitude_array.dtype.type, np.integer):
        amplitude_array = amplitude_array.astype(np.double)
    original_amp_shape = amplitude_array.shape
    amplitude_array = amplitude_array.squeeze()

    invalid_index_Null_image = amplitude_array == amplitudeNodeNullValue  # amp outside range

    amplitude_array[invalid_index_Null_image] = amplitude_array.mean()
    amplitude_array[(amplitude_array < amplitude_array.mean() - 3 * amplitude_array.std())] = (
        amplitude_array.mean() - 3 * amplitude_array.std()
    )
    amplitude_array[(amplitude_array > amplitude_array.mean() + 3 * amplitude_array.std())] = (
        amplitude_array.mean() + 3 * amplitude_array.std()
    )

    progressUpdate(value=0.3)

    outputtau = args.wlength_min
    output, lixo = filter_spiral(
        amplitude_array, T_depth, args.wlength_min, args.wlength_max, args.multip_factor, args.smoothstep_factor
    )

    output[invalid_index_Null_image] = args.nullable
    output = np.nan_to_num(output, nan=args.nullable, copy=False).reshape(original_amp_shape)

    # Get output node ID
    outputNodeID_std = args.outputVolume_std
    if outputNodeID_std is None:
        raise ValueError("Missing output node")

    writeDataInto(outputNodeID_std, output, mrml.vtkMRMLScalarVolumeNode, reference=amplitude)

    progressUpdate(value=1.0)

    print("Done")
