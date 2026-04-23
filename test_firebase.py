import os
import json
import firebase_admin
from firebase_admin import credentials
from dotenv import load_dotenv

load_dotenv()

FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")

print(f"PATH: {FIREBASE_CREDENTIALS_PATH}")
print(f"JSON starts with: {FIREBASE_CREDENTIALS_JSON[:20] if FIREBASE_CREDENTIALS_JSON else 'None'}")

try:
    if FIREBASE_CREDENTIALS_PATH and os.path.exists(FIREBASE_CREDENTIALS_PATH):
        print("Initializing from PATH...")
        cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
        print("Success!")
    elif FIREBASE_CREDENTIALS_JSON:
        print("Initializing from JSON...")
        # Strip potential single quotes if they were added by the env write
        json_str = FIREBASE_CREDENTIALS_JSON.strip("'")
        cred_dict = json.loads(json_str)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        print("Success!")
    else:
        print("No credentials found!")
except Exception as e:
    print(f"FAILED: {e}")
