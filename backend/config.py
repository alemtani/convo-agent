import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")

# The learner's current HSK ceiling — universal across topics, not authored per
# topic. Vocab up to and including this band is fair game everywhere; raising it
# unlocks higher-band words for every topic at once. Single-user default here;
# moves to the per-user profile when multi-user (build for one, design for many).
HSK_BAND_CEILING = int(os.getenv("HSK_BAND_CEILING", "2"))
