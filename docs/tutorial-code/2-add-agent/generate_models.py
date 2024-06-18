import shutil
from pathlib import Path

import pandas as pd
from muse.wizard import add_agent, get_sectors

parent_path = Path(__file__).parent


def generate_model_1():
    """Generates the first model for tutorial 1.

    Adds a new agent to the model with a single objective.

    """
    model_name = "1-single-objective"

    # Starting point: copy model from previous tutorial
    model_path = parent_path / model_name
    if model_path.exists():
        shutil.rmtree(model_path)
    shutil.copytree(parent_path / "../1-add-new-technology/2-scenario", model_path)

    # Copy agent A1 -> A2
    add_agent(
        model_path,
        agent_name="A2",
        copy_from="A1",
        agentshare_new="Agent2",
    )

    # Split population between the two agents
    agents_file = model_path / "technodata/Agents.csv"
    df = pd.read_csv(agents_file)
    df.loc[:, "Quantity"] = 0.5
    df.to_csv(agents_file, index=False)

    # Split capacity equally between the two agents
    for sector in get_sectors(model_path):
        technodata_file = model_path / f"technodata/{sector}/Technodata.csv"
        df = pd.read_csv(technodata_file)
        df.loc[1:, "Agent1"] = 0.5
        df.loc[1:, "Agent2"] = 0.5
        df.to_csv(technodata_file, index=False)


def generate_model_2():
    """Generates the second model for tutorial 2.

    Adds a second objective for agent A2.

    """
    model_name = "2-multiple-objective"

    # Starting point: copy model from previous tutorial
    model_path = parent_path / model_name
    if model_path.exists():
        shutil.rmtree(model_path)
    shutil.copytree(parent_path / "1-single-objective", model_path)

    # Add second objective for agent A2
    agents_file = model_path / "technodata/Agents.csv"
    df = pd.read_csv(agents_file)
    df.loc[df["Name"] == "A2", "Objective2"] = "EAC"
    df.loc[df["Name"] == "A2", "DecisionMethod"] = "weighted_sum"
    df.loc[df["Name"] == "A2", ["ObjData1", "ObjData2"]] = 0.5
    df.loc[df["Name"] == "A2", "Objsort2"] = True
    df.to_csv(agents_file, index=False)

    # Modify residential sector MaxCapacityGrowth
    technodata_file = model_path / "technodata/residential/Technodata.csv"
    df = pd.read_csv(technodata_file)
    df.loc[1:, "MaxCapacityGrowth"] = 0.4
    df.to_csv(technodata_file, index=False)


if __name__ == "__main__":
    generate_model_1()
    generate_model_2()
