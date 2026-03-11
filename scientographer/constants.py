from pathlib import Path 
import dotenv
import os

RAW_DATA_PATH = Path("data","raw")
RAW_DATA_PATH.mkdir(exist_ok=True)

PROCESSED_DATA_PATH = Path("data","processed")
PROCESSED_DATA_PATH.mkdir(exist_ok=True)

FIGURES_PATH = Path("reports","figures")
FIGURES_PATH.mkdir(exist_ok=True)

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
