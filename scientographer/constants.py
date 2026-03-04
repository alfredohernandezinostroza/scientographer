from pathlib import Path 
import dotenv
import os

RAW_DATA_PATH = Path("data","raw")
RAW_DATA_PATH.mkdir(exist_ok=True)

PROCESSED_DATA_PATH = Path("data","processed")
PROCESSED_DATA_PATH.mkdir(exist_ok=True)

FIGURES_PATH = Path("reports","figures")
FIGURES_PATH.mkdir(exist_ok=True)

dotenv.load_dotenv(Path('..','.env'))
EMAIL = os.getenv('MY_EMAIL')
DEFAULT_UI_PROJECT_ID = 1
DEFAULT_UI_USERNAME = os.getenv('DEFAULT_UI_USERNAME')
TEAM_NAME = os.getenv('TEAM_NAME')

