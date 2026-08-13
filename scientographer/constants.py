from pathlib import Path 
import dotenv
import os

RAW_DATA_PATH = Path("data","raw")
RAW_DATA_PATH.mkdir(exist_ok=True)

PROCESSED_DATA_PATH = Path("data","processed")
PROCESSED_DATA_PATH.mkdir(exist_ok=True)

GRAPH_LEVEL_DATA_PATH = Path("data","graph_level_data")
GRAPH_LEVEL_DATA_PATH.mkdir(exist_ok=True)

KEYWORDS_LEVEL_DATA_PATH = Path("data","keywords_level_data")
KEYWORDS_LEVEL_DATA_PATH.mkdir(exist_ok=True)

FIGURES_PATH = Path("reports","figures")
FIGURES_PATH.mkdir(exist_ok=True)

# Root for the retrieved full-text corpus (PDFs / XML). This lives OUTSIDE the repo
# because it runs to tens of GB and the repo volume is nearly full, while /raid has
# room. Small derived tables (manifests, parquet) still live under data/ so they can
# be DVC-tracked normally. Override with FULL_TEXT_DATA_ROOT in .env if the corpus
# needs to move; nothing here is created eagerly, since the volume may not be mounted
# on every machine that imports this module.
EXTERNAL_DATA_ROOT = Path(
    os.getenv("FULL_TEXT_DATA_ROOT", "/raid/fredi_dbs/papers-motor-learning-network")
)

DEFAULT_UI_PROJECT_ID = 1

read_dotenv = dotenv.load_dotenv(Path(__file__).parent.parent / '.env')
assert read_dotenv, f"Failed to read .env file ast {Path(__file__).parent.parent / '.env'}"
assert os.getenv("MY_EMAIL") is not None, "MY_EMAIL not found in .env file"
assert os.getenv("DEFAULT_UI_USERNAME") is not None, "DEFAULT_UI_USERNAME not found in .env file"
assert os.getenv("TEAM_NAME") is not None, "TEAM_NAME not found in .env file"
assert os.getenv("GOOGLE_DRIVE_FOLDER_ID") is not None, "GOOGLE_DRIVE_FOLDER_ID not found in .env file"
assert os.getenv("OPENCITATIONS_ACCESS_TOKEN") is not None, "OPENCITATIONS_ACCESS_TOKEN not found in .env file"

EMAIL = os.getenv('MY_EMAIL')
DEFAULT_UI_USERNAME = os.getenv('DEFAULT_UI_USERNAME')
TEAM_NAME = os.getenv('TEAM_NAME')
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
OPENCITATIONS_ACCESS_TOKEN = os.getenv('OPENCITATIONS_ACCESS_TOKEN')
OPENALEX_API_KEY = os.getenv('OPENALEX_API_KEY')
