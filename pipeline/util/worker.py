import time

from pipeline.util.logging import _print


class Worker:
    def __init__(self) -> None:
        pass

    def begin(self) -> None:
        _print("Starting worker")
        while True:
            print("true", flush=True)
            # new_data = sys.stdin.read()
            # if new_data == "alive_check":
            #    print("true")
            time.sleep(0.1)
