"""Linux 干净容器验收入口：实现位于 checks/。"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks.linux_check import main

if __name__ == "__main__":
    main()
