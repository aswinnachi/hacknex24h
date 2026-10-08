import platform
import sys

def detect_hardware():
    info = {
        "python_version": sys.version.split(" ")[0],
        "os": platform.system(),
        "cpu": True,
        "cuda": False,
        "mps": False,
        "primary_device": "cpu"
    }

    try:
        import torch
        if torch.cuda.is_available():
            info["cuda"] = True
            info["primary_device"] = "cuda"
        elif torch.backends.mps.is_available():
            info["mps"] = True
            info["primary_device"] = "mps"
    except ImportError:
        pass # torch not installed yet

    return info

if __name__ == "__main__":
    print(detect_hardware())
