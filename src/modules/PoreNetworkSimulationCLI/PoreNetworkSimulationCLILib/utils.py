import os
import csv
import numpy as np
import openpnm


def manual_valvatne_blunt(pore_network):
    """
    Modifies a PoreSpy format pore network dictionary inplace, adding Valvatne
    Blunt flow properties:
        throat.manual_valvatne_conductivity
        throat.manual_valvatne_conductance
        pore.manual_valvatne_conductivity
        pore.shape

    Shape values:
        0 - Triangle
        1 - Square
        2 - Circle

        In this context, conductivity is linear (1D), therefore,
    to get conductance, it must be divided by length, but the
    cross sectional area is already computed in the value
        Conductance already takes in account the throat mid length
    and the connected pores half-lengths
    """
    throat_shape_factor = pore_network["throat.shape_factor"]
    throat_radius = pore_network["throat.inscribed_diameter"] / 2
    throat_area = pore_network["throat.cross_sectional_area"]
    throat_conns_0_length = pore_network["throat.conns_0_length"]
    throat_conns_1_length = pore_network["throat.conns_1_length"]
    throat_mid_length = pore_network["throat.mid_length"]
    pore_shape_factor = pore_network["pore.shape_factor"]
    pore_radius = pore_network["pore.extended_diameter"] / 2
    pore_area = pore_radius**2 / (4 * pore_shape_factor)

    pore_shape = np.zeros(len(pore_network["pore.extended_diameter"]), dtype=np.uint8)
    pore_conductivity = np.zeros(len(pore_network["pore.extended_diameter"]), dtype=np.float32)
    for pore, shape_factor in enumerate(pore_network["pore.shape_factor"]):
        if shape_factor <= 0.048:
            pore_shape[pore] = 0
            pore_conductivity[pore] = (3 / 5) * pore_area[pore] ** 2 * shape_factor
        elif shape_factor <= 0.07:
            pore_shape[pore] = 1
            pore_conductivity[pore] = (0.5623) * pore_area[pore] ** 2 * shape_factor
        else:
            pore_shape[pore] = 2
            pore_conductivity[pore] = (1 / 8) * pore_area[pore] * pore_radius[pore] ** 2

    throat_shape = np.zeros(len(pore_network["throat.shape_factor"]), dtype=np.uint8)
    throat_conductivity = np.zeros(len(pore_network["throat.shape_factor"]), dtype=np.float32)
    throat_conductance = np.zeros(len(pore_network["throat.shape_factor"]), dtype=np.float32)
    for throat, shape_factor in enumerate(throat_shape_factor):
        conn_0 = pore_network["throat.conns"][throat][0]
        conn_1 = pore_network["throat.conns"][throat][1]
        if shape_factor <= 0.048:
            throat_shape[throat] = 0
            throat_conductivity[throat] = (3 / 5) * throat_area[throat] ** 2 * shape_factor
        elif shape_factor <= 0.07:
            throat_shape[throat] = 1
            throat_conductivity[throat] = (0.5623) * throat_area[throat] ** 2 * shape_factor
        else:
            throat_shape[throat] = 2
            throat_conductivity[throat] = (1 / 8) * throat_area[throat] * throat_radius[throat] ** 2
        throat_conductance[throat] = (
            throat_conns_0_length[throat] / pore_conductivity[conn_0]
            + throat_mid_length[throat] / throat_conductivity[throat]
            + throat_conns_1_length[throat] / pore_conductivity[conn_1]
        ) ** (-1)

    pore_network["throat.shape"] = throat_shape
    pore_network["throat.manual_valvatne_conductivity"] = throat_conductivity
    pore_network["throat.manual_valvatne_conductance"] = throat_conductance
    pore_network["pore.shape"] = pore_shape
    pore_network["pore.manual_valvatne_conductivity"] = pore_conductivity

    return


def set_subresolution_conductance(sub_network, subresolution_function):

    sub_network["pore.diameter"] = sub_network["pore.equivalent_diameter"]
    sub_network["throat.diameter"] = sub_network["throat.equivalent_diameter"]

    # Equations
    pressure2radius = lambda Pc: -2 * 480 * np.cos(np.pi * 140 / 180) / Pc
    area_function = lambda r: np.pi * r**2

    # Pore conductivity
    pore_conductivity_resolved = sub_network["pore.manual_valvatne_conductivity"]
    pore_phi = sub_network["pore.subresolution_porosity"]
    pore_pressure = np.array([subresolution_function(p) for p in pore_phi])
    pore_pressure[pore_phi == 1] = np.array([pressure2radius(r) for r in sub_network["pore.diameter"] / 2])[
        pore_phi == 1
    ]

    pore_capilar_radius = np.array([pressure2radius(Pc) for Pc in pore_pressure])
    sub_network["pore.capilar_radius"] = pore_capilar_radius

    pore_number_of_capilaries = (area_function(sub_network["pore.diameter"] / 2) * pore_phi) / area_function(
        pore_capilar_radius
    )
    sub_network["pore.number_of_capilaries"] = pore_number_of_capilaries
    pore_conductivity = (1 / 8) * np.pi * pore_capilar_radius**4
    pore_conductivity *= pore_number_of_capilaries

    throat_phi = sub_network["throat.subresolution_porosity"]
    throat_pressure = np.array([subresolution_function(p) for p in throat_phi])
    throat_pressure[throat_phi == 1] = np.array([pressure2radius(r) for r in sub_network["throat.diameter"] / 2])[
        throat_phi == 1
    ]
    throat_capilar_radius = np.array([pressure2radius(Pc) for Pc in throat_pressure])
    throat_number_of_capilaries = (area_function(sub_network["throat.diameter"] / 2) * throat_phi) / area_function(
        throat_capilar_radius
    )
    throat_conductivity = (1 / 8) * np.pi * throat_capilar_radius**4
    throat_conductivity *= throat_number_of_capilaries

    # Throat conductance
    throat_conductance = np.copy(sub_network["throat.manual_valvatne_conductance"])
    for throat_index, (left_index, right_index) in enumerate(
        sub_network["throat.conns"],
    ):
        left_unresolved = sub_network["throat.phases"][throat_index][0] == 2
        right_unresolved = sub_network["throat.phases"][throat_index][1] == 2

        if left_unresolved and not right_unresolved:
            throat_conductance[throat_index] = sub_network["throat.mid_length"][throat_index] / (
                2 * throat_conductivity[throat_index]
            )
            throat_conductance[throat_index] += (
                sub_network["throat.conns_0_length"][throat_index] / pore_conductivity[left_index]
            )
            throat_conductance[throat_index] += (
                sub_network["throat.conns_1_length"][throat_index] / pore_conductivity_resolved[right_index]
            )
            throat_conductance[throat_index] **= -1

        elif right_unresolved and not left_unresolved:
            throat_conductance[throat_index] = sub_network["throat.mid_length"][throat_index] / (
                2 * throat_conductivity[throat_index]
            )
            throat_conductance[throat_index] += (
                sub_network["throat.conns_0_length"][throat_index] / pore_conductivity_resolved[left_index]
            )
            throat_conductance[throat_index] += (
                sub_network["throat.conns_1_length"][throat_index] / pore_conductivity[right_index]
            )
            throat_conductance[throat_index] **= -1

        elif right_unresolved and left_unresolved:
            throat_conductance[throat_index] = (
                sub_network["throat.mid_length"][throat_index] / throat_conductivity[throat_index]
            )
            throat_conductance[throat_index] += (
                sub_network["throat.conns_0_length"][throat_index] / pore_conductivity[left_index]
            )
            throat_conductance[throat_index] += (
                sub_network["throat.conns_1_length"][throat_index] / pore_conductivity[right_index]
            )
            throat_conductance[throat_index] **= -1

    sub_network["pore.cap_pressure"] = pore_pressure.copy()
    sub_network["pore.cap_radius"] = pore_capilar_radius.copy()
    sub_network["throat.cap_pressure"] = throat_pressure.copy()
    sub_network["throat.cap_radius"] = throat_capilar_radius.copy()
    sub_network["throat.sub_conductivity"] = throat_conductivity.copy()
    sub_network["pore.sub_conductivity"] = pore_conductivity.copy()
    sub_network["throat.manual_valvatne_conductance_former"] = throat_conductance.copy()
    sub_network["throat.manual_valvatne_conductance"] = throat_conductance
    sub_network["throat.number_of_capilaries"] = throat_number_of_capilaries
    sub_network["pore.number_of_capilaries"] = pore_number_of_capilaries

    sub_network["throat.cross_sectional_area"] = np.pi * sub_network["throat.cap_radius"] ** 2
    sub_network["throat.volume"] = sub_network["throat.total_length"] * sub_network["throat.cross_sectional_area"]
    sub_network["pore.volume"] *= sub_network["pore.subresolution_porosity"]

    print(os.getcwd())
    for element in ("pore.", "throat."):
        pore_keys = [key for key in sub_network.keys() if key.startswith(element)]
        pore_dict = {key: sub_network[key] for key in pore_keys}
        csv_file_name = f"output_{element[:-1]}.csv"

        with open(csv_file_name, "w", newline="") as csvfile:
            # Use filtered_keys as fieldnames to ensure only "pore" keys are included
            fieldnames = pore_keys
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader()

            for i in range(len(pore_dict[pore_keys[0]])):
                row_data = {key: pore_dict[key][i] for key in pore_keys}
                writer.writerow(row_data)


def _counter():
    i = -1
    while True:
        i += 1
        yield i


def get_clusters(network):
    """
    clusters are numbered starting at 0
    """
    from scipy.sparse import csgraph as csg

    am = network.create_adjacency_matrix(fmt="coo", triu=True)
    N, Cs = csg.connected_components(am, directed=False)
    return N, Cs


def get_sub_spy(spy_network, sub_pores, sub_throats):

    sub_pn = {}
    for prop in spy_network.keys():
        if prop.split(".")[0] == "pore":
            sub_pn[prop] = spy_network[prop][sub_pores]
        else:
            sub_pn[prop] = spy_network[prop][sub_throats]

    counter = _counter()
    f_counter = lambda x: next(counter) if x else 0
    new_pore_index = np.fromiter(map(f_counter, sub_pores), dtype="int")

    if len(sub_pn["throat.conns"]) == 0:
        return False
    for i in np.nditer(sub_pn["throat.conns"], op_flags=["readwrite"]):
        i[...] = new_pore_index[i]
    return sub_pn


def get_connected_spy_network(network, in_face, out_face):
    """
    in_face, out_face: str
        Each must be one of 'xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax'
    """
    valid_inputs = ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax"]
    if in_face not in valid_inputs:
        raise ValueError(f"Face values is invalid: in_face = {in_face}")
    if out_face not in valid_inputs:
        raise ValueError(f"Face values is invalid: out_face = {out_face}")

    _, cluster_labels = get_clusters(network)
    in_labels = np.unique(cluster_labels[network[f"pore.{in_face}"]])
    out_labels = np.unique(cluster_labels[network[f"pore.{out_face}"]])
    common_labels = np.intersect1d(in_labels, out_labels, assume_unique=True)

    connected_pores = network.pores()[np.isin(cluster_labels, common_labels)]
    connected_throats = network.throats()[np.isin(network["throat.conns"], connected_pores).all(axis=1)]

    return np.isin(cluster_labels, common_labels), np.isin(network["throat.conns"], connected_pores).all(axis=1)


def single_phase_permeability(
    pore_network,
    throat_shape=None,
    pore_shape=None,
    in_face="xmin",
    out_face="xmax",
    subresolution_function=None,
):

    if (pore_network[f"pore.{in_face}"].sum() == 0) or (pore_network[f"pore.{out_face}"].sum() == 0):
        return (0, None, None)

    is_multiscale = pore_network["pore.phase2"].any()
    if is_multiscale and (subresolution_function is None):
        print("Multiscale network with no subresolution function")
        return (0, None, None)

    proj = openpnm.io.network_from_porespy(pore_network)
    connected_pores, connected_throats = get_connected_spy_network(proj.network, in_face, out_face)
    sub_network = get_sub_spy(pore_network, connected_pores, connected_throats)
    if sub_network is False:
        return 0, None, None
    for prop in sub_network.keys():
        np.nan_to_num(sub_network[prop], copy=False)

    manual_valvatne_blunt(sub_network)
    print("multiscale: ", is_multiscale)
    if is_multiscale:
        pass
    set_subresolution_conductance(sub_network, subresolution_function)
    sub_proj = openpnm.io.network_from_porespy(sub_network)
    water = openpnm.phase.Water(network=sub_proj.network)
    water.add_model_collection(openpnm.models.collections.physics.standard)
    sub_proj["throat.hydraulic_conductance"] = sub_proj["throat.manual_valvatne_conductance"]
    sub_proj["pore.phase"][...] = 1
    print("Hidr Cond: :", sub_proj["throat.hydraulic_conductance"])
    perm = openpnm.algorithms.StokesFlow(
        network=sub_proj,
        phase=water,
    )
    perm.settings["f_rtol"] = 1e-11
    perm.settings["x_rtol"] = 1e-11
    # print("\n\n############## OpenPNM flow ###########\n\n", perm, "\n\n##############################\n\n")

    perm.set_value_BC(
        pores=sub_proj.pores(in_face), values=101325, mode="overwrite"
    )  # pressure in pa: 101325 pa = 1 atm
    perm.set_value_BC(pores=sub_proj.pores(out_face), values=0, mode="overwrite")

    perm.run(verbose=True)

    project = perm.project
    pore_dict = {}
    throat_dict = {}
    for l in range(len(project)):
        for p in project[l].props():
            # if slicer_is_in_developer_mode():
            #    print(p, type(project[l][p]), project[l][p])
            prop_array = project[l][p]
            if prop_array.ndim == 1:
                if p[:4] == "pore":
                    pore_dict[p] = project[l][p]
                else:
                    throat_dict[p] = project[l][p]
            else:
                for i in range(prop_array.shape[1]):
                    if p[:4] == "pore":
                        pore_dict[f"{p}_{i}"] = project[l][p][:, i]
                    else:
                        throat_dict[f"{p}_{i}"] = project[l][p][:, i]

    return (perm, pore_dict, throat_dict)
