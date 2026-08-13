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

# Elsevier / ScienceDirect TDM. Deliberately no assert: the publisher routes are
# optional, so this module must still import on machines that never enable them.
# The key only *identifies* the caller -- it does not by itself grant access to
# subscribed content; that needs entitlement via an institutional IP or InstToken.
ELSEVIER_API_KEY = os.getenv('ELSEVIER_API_KEY')
ELSEVIER_INSTITUTIONAL_TOKEN = os.getenv('ELSEVIER_INSTITUTIONAL_TOKEN')

# Wiley text-and-data-mining. Sent as the Wiley-TDM-Client-Token header; issued by
# the library rather than self-served, and normally still IP-restricted.
WILEY_TDM_TOKEN = os.getenv('WILEY_TDM_TOKEN')

# Springer Nature issues separate keys per API product rather than one key: the
# Metadata API covers bibliographic records for the whole catalogue, while the
# Open Access API is the one that returns full text -- and only for open content.
# Neither reaches subscribed full text on its own; that needs a TDM agreement.
SPRINGER_NATURE_METADATA = os.getenv('SPRINGER_NATURE_METADATA')
SPRINGER_NATURE_OPEN_ACCESS = os.getenv('SPRINGER_NATURE_OPEN_ACCESS')

# Taylor & Francis matters more than Wiley for this corpus (Journal of Motor
# Behavior, Research Quarterly for Exercise and Sport), but unlike Elsevier and
# Springer they are not known to self-serve a TDM key -- access is normally an
# institutional agreement, and the generic Crossref TDM + IP-entitlement route may
# cover them without any key at all. Slot declared; leave unset until confirmed.
TAYLOR_AND_FRANCIS_TDM_TOKEN = os.getenv('TAYLOR_AND_FRANCIS_TDM_TOKEN')
