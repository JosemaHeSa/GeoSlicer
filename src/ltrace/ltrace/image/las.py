import numpy as np
import os
import lasio
import slicer
from ltrace.slicer.helpers import (
    getVolumeNullValue,
    arrayFromVisibleSegmentsBinaryLabelmap,
)
from ImageLogExportLib.ImageLogCSV import _arrayPartsFromNode
from pathlib import Path
import pandas as pd
import datetime
import re
import logging
from dataclasses import dataclass
from DLISImportLib.DLISImportLogic import WELL_NAME_TAG, UNITS_TAG

import ltrace.image.lasGeologFile as lasGeologFile


def retrieve_depth_curve(node_list):
    def extract_depth_info_from_node(node):
        lasdata, depths = _extract_las_data_from_node(node)
        data = lasdata["data"].squeeze()

        # Slicer world is in mm. Converting to meters:
        step = lasdata["step"] / 1000.0
        origin = lasdata["origin"] / 1000.0

        if isinstance(node, slicer.vtkMRMLTableNode):
            return depths
        else:
            min_depth = -1 * origin
            max_depth = min_depth + step * (data.shape[0])
            depths = np.linspace(min_depth, max_depth, data.shape[0])
            return depths

    def check_single_depth_list(depths_all):
        depth_disparity_tolerance = 5e-02
        for i in range(len(depths_all) - 1):
            subtr = np.subtract(depths_all[i], depths_all[i + 1])
            assert len(depths_all[0][abs(subtr) > depth_disparity_tolerance]) == 0

    depths_all = [[]]
    depths = []
    # We prefer to read the depths from a table instead of calculating the depths from a volume...
    for i, node in enumerate(node_list):
        depths_all.append(extract_depth_info_from_node(node))
        if isinstance(node, slicer.vtkMRMLTableNode) and len(depths) == 0:
            depths = depths_all[-1].copy()
    #  ... but we calculate them from a volume node if there's no table in the scene
    if not len(depths):
        for i, node in enumerate(node_list):
            depths_all.append(extract_depth_info_from_node(node))
            if len(depths) == 0:
                depths = depths_all[-1].copy()

    depths_all = depths_all[1:]  # getting rid of first empty element allocated at declaration

    try:
        check_single_depth_list(depths_all)
    except:
        raise RuntimeError(
            f"Error: Can't export to a LAS file curves obtained in different depths. If you imported from DLIS, try to export different frames or logical files to different LAS files."
        )

    return depths


def export_las(
    node_list,
    output_path,
    version=2,
    well_name="",
    well_id="",
    field_name="",
    company_name="",
    producer_name="",
    file_id="",
):
    lasfile = lasGeologFile.lasGeologFile()  # lasio.LASFile
    lasfile.well.DATE = datetime.date.today().strftime("%Y-%m-%d %H:%M:%S")
    lasfile.well.WELL = well_name
    lasfile.well.UWY = well_id
    lasfile.well.FLD = field_name
    lasfile.well.COMP = company_name  # the company which the log was produced for
    lasfile.well.SRVC = producer_name  # DLIS: "producer's name"; LAS: The logging company
    lasfile.other = "Generated by GeoSlicer"

    # Retrieve the depths one single time
    depths = retrieve_depth_curve(node_list)

    # (When appending the DEPT curve, WELL.STRT and WELL.STEP will be set automatically by lasio)
    if depths.any():
        lasfile.append_curve("DEPT", depths, unit="m", descr=" ")
    else:
        raise RuntimeError(
            f"Error exporting to LAS: not possible to get depths. Please check if you selected valid nodes to export."
        )

    # Adding the other data curves extracted from the nodes
    las_well_name = ""
    count_well = 0
    for i, node in enumerate(node_list):
        try:
            las_data, depths = _extract_las_data_from_node(node)
        except:
            raise RuntimeError(f"Error exporting to LAS: can't extract data from node {node.GetName()}")
        las_info = extract_las_info_from_node(node)

        # We expect a single well per file - check that
        lasfile.well.WELL = las_info["well_name"]
        if las_info["well_name"] != las_well_name:
            las_well_name = las_info["well_name"]
            count_well += 1
        if count_well == 2:
            raise RuntimeError("Error exporting to LAS. You can't export nodes from different Wells to the same file.")

        add_curve(lasfile, las_data, las_info)

    lasfile.write(output_path, version=version)


# TODO - MUSA-76  Consider condensing las_data and las_info in a single dataclass
def add_curve(lasfile, las_data, las_info):

    data = las_data["data"].squeeze()

    data[data == las_info["null_value"]] = -999.25  # TODO MUSA-75 Update invalid data handling policy

    if data.ndim > 1:
        if data.ndim == 1:
            image_width = 1
        else:
            image_width = data.shape[1]
        for i in range(0, image_width):
            curve_data = data[:, i]
            lasfile.append_curve(
                (las_info["data_name"]).strip() + "[{0}]".format(i + 1),
                curve_data,
                las_info["units"],
                descr=f"{{AF}}",  # {AF} stands for "array of floats"
            )
    else:
        lasfile.append_curve(las_info["data_name"], data, las_info["units"], descr=" ")


def _extract_las_data_from_node(node):
    las_data = {}
    depths = []
    if isinstance(node, slicer.vtkMRMLSegmentationNode):
        las_data["data"], spacing, origin = arrayFromVisibleSegmentsBinaryLabelmap(node)
        las_data["step"] = spacing[2]
        las_data["origin"] = origin[2]
    elif isinstance(node, slicer.vtkMRMLTableNode):
        depths, las_data["data"] = _arrayPartsFromNode(node)
        las_data["step"] = depths[1] - depths[0]
        las_data["origin"] = depths[0]
    else:
        las_data["data"] = slicer.util.arrayFromVolume(node)
        las_data["step"] = node.GetSpacing()[2]
        las_data["origin"] = node.GetOrigin()[2]

    return las_data, depths


def extract_las_info_from_node(node):

    las_info = {}

    #  units
    units_search = re.search(r"\[(.*?)\]", node.GetName())
    units_from_name = units_search.group(1) if units_search else "NONE"
    units = units_from_name

    if node.GetAttribute(UNITS_TAG) is not None:
        units = node.GetAttribute(UNITS_TAG)
        if units_from_name == "NONE":
            logging.info(
                f"Node name ({node.GetName()}) doesn't include its units ({node.GetAttribute(UNITS_TAG)}) in it."
            )
        elif node.GetAttribute(UNITS_TAG) != units_from_name:
            logging.warning(
                f"Units informed in {node.GetName()} ({node.GetAttribute(UNITS_TAG)}) metadata are different from the units implied by the node name ({units_from_name}). {node.GetAttribute(UNITS_TAG)} will be considered as the units."
            )
    else:
        units = units_from_name
        if units_from_name == "NONE":
            logging.warning(f"No units found for {node.GetName()}. They'll be set to value 'NONE'")

    las_info["units"] = units

    #  well name
    well_from_node_name = node.GetName().split("_")[0] if len(node.GetName().split("_")) > 1 else ""
    if node.GetAttribute(WELL_NAME_TAG) is not None:
        las_info["well_name"] = node.GetAttribute(WELL_NAME_TAG)
        if well_from_node_name == "":
            logging.info(
                f"Node name ({node.GetName()}) doesn't have the well name ({node.GetAttribute(WELL_NAME_TAG)}) prepended to it."
            )
        elif node.GetAttribute(WELL_NAME_TAG) != well_from_node_name:
            logging.warning(
                f"Well name informed in {node.GetName()} ({node.GetAttribute(WELL_NAME_TAG)}) metadata is different from the well name implied by the node name ({well_from_node_name}). {node.GetAttribute(WELL_NAME_TAG)} will be considered as the well name."
            )
    else:
        las_info["well_name"] = well_from_node_name
        if well_from_node_name == "":
            logging.warning(f"No well name found for {node.GetName()}.")

    las_info["data_name"] = node.GetName().replace("[" + units + "]", "")
    new_data_name = las_info["data_name"]
    if node.GetAttribute(WELL_NAME_TAG):
        new_data_name = las_info["data_name"].replace(node.GetAttribute(WELL_NAME_TAG) + "_", "")
    if len(new_data_name) == 0:
        new_data_name = node.GetAttribute(WELL_NAME_TAG)
    las_info["data_name"] = new_data_name

    las_info["null_value"] = (
        0
        if isinstance(node, slicer.vtkMRMLLabelMapVolumeNode) or isinstance(node, slicer.vtkMRMLSegmentationNode)
        else getVolumeNullValue(node)
    )

    subject_hierarchy_node = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
    item_parent = subject_hierarchy_node.GetItemParent(subject_hierarchy_node.GetItemByDataNode(node))
    directory_name = subject_hierarchy_node.GetItemName(item_parent)

    las_info["frame_name"] = directory_name

    return las_info
