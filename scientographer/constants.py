from pathlib import Path 
import dotenv
import os

DATA_PATH = Path("data","raw")
DATA_PATH.mkdir(exist_ok=True)

dotenv.load_dotenv(Path('..','.env'))
MY_EMAIL = os.getenv('MY_EMAIL')
