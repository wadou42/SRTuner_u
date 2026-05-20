import statistics

import subprocess
import re
import os

from utils.line_level_analysis import LineAnalysis

def set_affinity_range(start_core, end_core, silent=True):
    """
    Sets the current process affinity to a range of CPU cores.
    :param start_core: The starting index of the core (inclusive)
    :param end_core: The ending index of the core (inclusive)
    """
    try:
        core_ids = set(range(start_core, end_core + 1))
        
        os.sched_setaffinity(0, core_ids)
        
        actual_cores = os.sched_getaffinity(0)
        if not silent:
            print(f"Success: Script is now allowed to run on cores: {actual_cores}")
    except ValueError:
        print("Error: Invalid core range provided.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

class SnappyManager():
    def __init__(self, cpu_cores:int, build_dir: str, lzbench_home: str, num_repeat: int = 1, enable_gperftools: bool = False) -> None:
        self.cpu_cores = cpu_cores
        self.build_dir = build_dir
        self.num_repeat = num_repeat
        self.lzbench_home = lzbench_home
        self.enable_gperftools = enable_gperftools

    def build(self, opt_config: str = "-g -O3") -> int:
        self.clean()
        set_affinity_range(self.cpu_cores, self.cpu_cores+31, silent=True)
        build_commands = f"""
            mkdir -p build && cd build &&
            cmake .. \
            -DCMAKE_BUILD_TYPE=Release \
            -DBUILD_SHARED_LIBS=ON \
            -DCMAKE_CXX_FLAGS="{opt_config}" \
            && make -j32
            """
        p = subprocess.run(
            build_commands,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            cwd=self.build_dir,
        )
        if p.returncode != 0:
            return -1
        return 0

    def parser_results(self, raw_output: str) -> tuple[float, float]:
        # TODO: improve the regex to be more robust
        for line in raw_output.splitlines():
            if match := re.search(
                # snappy 2020-07-11         584 MB/s  1165 MB/s   115770756  54.62 input_files/silesia.tar
                r"snappy [\d\-]+ +([\d\.]+) MB/s +([\d\.]+) MB/s", line
                # r"snappy [\d\.]+ -\d+\s+([\d\.]+) MB/s\s+([\d\.]+) MB/s", line
            ):
                compress_speed = float(match.group(1))
                decompress_speed = float(match.group(2))
                return compress_speed, decompress_speed
        return -1, -1

    def test(self, num_repeat: int = -1) -> float:
        set_affinity_range(self.cpu_cores, self.cpu_cores, silent=True)
        if num_repeat == -1:
            num_repeat = self.num_repeat
        compress_results: list[float] = []
        decompress_results: list[float] = []
        for _ in range(0, num_repeat):
            subprocess.run("sleep 1", shell=True, cwd=self.build_dir)
            p = subprocess.run(
                f"SNAPPY_HOME={self.build_dir} USE_GPERFTOOLS={1 if self.enable_gperftools else 0} bash run.sh ",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.build_dir,
            )
            compress, decompress = self.parser_results(
                p.stdout if p.stdout else ""
            )
            if p.returncode != 0:
                return -1
            compress_results.append(compress)
            decompress_results.append(decompress)

        def _median(lst):
            lst_copy = lst[:]
            n = len(lst_copy)
            if n == 0:
                return -1
            lst_copy.sort()
            mid = n // 2
            if n % 2 == 0:
                return (lst_copy[mid - 1] + lst_copy[mid]) / 2.0
            else:
                return lst_copy[mid]

        print(f"Compress speeds: {compress_results}")
        print(f"Decompress speeds: {decompress_results}")
        print(f"Median compress speed: {_median(compress_results)} MB/s")
        print(f"Median decompress speed: {_median(decompress_results)} MB/s")
        return _median(compress_results)

    def clean(self) -> int:
        clean_commands = "rm -rf build"
        p = subprocess.run(clean_commands, shell=True, cwd=self.build_dir)
        if p.returncode != 0:
            return -1
        return 0


if __name__ == "__main__":
    manager0 = SnappyManager(
        cpu_cores=0,
        build_dir="/home/whq/dataset/snappy/snappy-instance1",
        lzbench_home="/home/whq/dataset/lzbench",
        num_repeat=1,
        enable_gperftools=False,
    )
    manager0.clean()
    manager0.build(opt_config="-O3 -g")
    result_100 = []
    for i in range(0, 100):
        result = manager0.test(1)
        result_100.append(result)
        print(f"Run {i+1}/100: {result} MB/s", flush=True)
    
    print(statistics.median(result_100))
    print(result_100)
    # manager.clean()
    
    


# Compress speeds: [173.0, 176.0, 176.0, 176.0, 177.0, 183.0, 175.0, 173.0, 178.0, 181.0]
# Decompress speeds: [1036.0, 1037.0, 1034.0, 1038.0, 1036.0, 1036.0, 1036.0, 1036.0, 1034.0, 1032.0]
