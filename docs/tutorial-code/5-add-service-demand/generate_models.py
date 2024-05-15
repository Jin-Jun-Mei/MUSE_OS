# Follows similar process as 1-add-new-technology


import os
import shutil
import sys

import pandas as pd
from muse.wizard import add_new_commodity, add_new_process

parent_path = os.path.dirname(os.path.abspath(sys.argv[0]))


"""
Model 1 - New service demand

"""


def generate_model_1():
    model_name = "1-new-service-demand"

    # Starting point: copy model from tutorial 4
    model_path = os.path.join(parent_path, model_name)
    if os.path.exists(model_path):
        shutil.rmtree(model_path)
    shutil.copytree(
        os.path.join(parent_path, "../4-modify-timing-data/1-modify-timeslices"),
        model_path,
    )

    # Copy gas commodity in power sector -> cook
    add_new_commodity(model_path, "cook", "residential", "heat")

    # Modify cook projections
    projections_file = os.path.join(model_path, "input/Projections.csv")
    df = pd.read_csv(projections_file)
    df.loc[1:, "cook"] = 100
    df.to_csv(projections_file, index=False)

    # Copy windturbine process in power sector -> cook
    add_new_process(model_path, "estove", "residential", "heatpump")
    add_new_process(model_path, "gas_stove", "residential", "gasboiler")

    # Modify output commodities
    commout_file = os.path.join(model_path, "technodata/residential/CommOut.csv")
    df = pd.read_csv(commout_file)
    df.loc[1:, "cook"] = 0
    df.loc[df["ProcessName"] == "gas_stove", df.columns[-6:]] = [0, 0, 0, 50, 0, 1]
    df.loc[df["ProcessName"] == "estove", df.columns[-6:]] = [0, 0, 0, 0, 0, 1]
    df.to_csv(commout_file, index=False)

    # Modify input commodities
    commin_file = os.path.join(model_path, "technodata/residential/CommIn.csv")
    df = pd.read_csv(commin_file)
    df.loc[1:, "cook"] = 0
    df.to_csv(commin_file, index=False)

    # Change cap_par, Fuel and EndUse
    technodata_file = os.path.join(model_path, "technodata/residential/technodata.csv")
    df = pd.read_csv(technodata_file)
    df.loc[df["ProcessName"] == "gas_stove", "cap_par"] = 8.8667
    df.loc[df["ProcessName"] == "gas_stove", "Fuel"] = "gas"
    df.loc[df["ProcessName"] == "electric_stove", "Fuel"] = "electricity"
    df.loc[df["ProcessName"] == "gas_stove", "EndUse"] = "cook"
    df.loc[df["ProcessName"] == "electric_stove", "EndUse"] = "cook"
    df.to_csv(technodata_file, index=False)

    # Change power sector limits
    technodata_file = os.path.join(model_path, "technodata/power/technodata.csv")
    df = pd.read_csv(technodata_file)
    df.loc[1:, "MaxCapacityAddition"] = 4
    df.loc[1:, "MaxCapacityGrowth"] = 0.4
    df.loc[1:, "TotalCapacityLimit"] = 60
    df.to_csv(technodata_file, index=False)

    # # Change gas supply limits
    # technodata_file = os.path.join(model_path, "technodata/gas/technodata.csv")
    # df = pd.read_csv(technodata_file)
    # df.loc[df["ProcessName"] == "gassupply1", "MaxCapacityAddition"] = 100
    # df.loc[df["ProcessName"] == "gassupply1", "MaxCapacityGrowth"] = 5


if __name__ == "__main__":
    generate_model_1()
