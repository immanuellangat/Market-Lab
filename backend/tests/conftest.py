import os
import tempfile

# Keep test runs away from the real data folder (must happen before `app` is imported).
os.environ["MARKETLAB_DATA"] = tempfile.mkdtemp(prefix="marketlab-test-")
os.environ["MARKETLAB_NO_LIVE"] = "1"
