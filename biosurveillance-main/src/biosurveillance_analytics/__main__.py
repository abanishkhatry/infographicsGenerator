import sys
from app import main

if __name__ == "__main__":
    print(sys.argv)
    if len(sys.argv) < 3:
        main('cdc', 'wslh', '999')
    else:
        main(*sys.argv)