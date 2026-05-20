import os
import re
import time
import random
import argparse
import subprocess
from pathlib import Path
from utils.line_level_analysis import LineAnalysis

FLOAT_MAX = float("inf")

class PolybenchManager:
    def __init__(
        self,
        build_home,
        cpucore=319,
        gcc_path="gcc",
        include_path="",
        exec_param="",
        num_repeats=1,
        enable_gperftools=False,
    ):
        self.gcc_path = gcc_path
        self.include_path = include_path
        self.artifact = "a.out"
        self.build_home = Path(build_home)
        self.cpucore = cpucore
        self.exec_param = exec_param
        self.num_repeats = num_repeats
        self.enable_gperftools = enable_gperftools

    def build(self, str_opt_setting):
        """ """
        commands = f"""
        taskset -c {self.cpucore} {self.gcc_path} -O2 {str_opt_setting} -c {self.include_path} {self.build_home}/*.c;
        taskset -c {self.cpucore} {self.gcc_path} -o {self.artifact} -O2 {str_opt_setting} *.o -lm;
        """
        subprocess.Popen(
            commands, stdout=subprocess.PIPE, shell=True, cwd=self.build_home, stderr=subprocess.PIPE
        ).wait()

        # Check if build fails
        if not os.path.exists(self.build_home / self.artifact):
            print(f"Build failed for {self.build_home}")
            return -1
        return 0

    def test(self):
        if self.enable_gperftools:
            run_commands = f"""
            time LD_PRELOAD=/usr/local/lib/libprofiler.so.0 CPUPROFILE=./main.prof CPUPROFILE_FREQUENCY=100  taskset -c {self.cpucore}  ./{self.artifact} {self.exec_param} ;
            # pprof --lines {self.artifact} main.prof* >> report.txt ;
            for f in main.prof*; do
                echo "processing $f"
                pprof --lines {self.artifact} "$f" >> report.txt || echo "failed processing $f" ;
                rm -f "$f" ;
            done
            """
        else:
            run_commands = f"""
            time taskset -c {self.cpucore} ./{self.artifact} {self.exec_param} ;
            """
        tot = 0

        for _ in range(self.num_repeats):
            p = subprocess.Popen(
                run_commands,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                cwd=self.build_home,
            )
            p.wait()
            if p.returncode != 0 or p.stderr is None:
                return FLOAT_MAX
            else:
                stderrs = p.stderr.read().decode("ascii").split("\n")

            for out in stderrs:
                if out.startswith("real"):
                    out = out.replace("real\t", "")
                    nums = re.findall(r"\d*\.?\d+", out)
                    assert len(nums) == 2, "Expect %dm %ds format"
                    secs = float(nums[0]) * 60 + float(nums[1])
                    tot += secs
        return tot

    def clean(self):
        commands = f"""
        taskset -c {self.cpucore} rm -rf *.o *.I *.s {self.artifact} main.prof* report.txt;
        """
        subprocess.Popen(
            commands, stdout=subprocess.PIPE, shell=True, cwd=self.build_home
        ).wait()
        
    @staticmethod
    def analysis_report(management1: "PolybenchManager", management2: "PolybenchManager"):
        func_file1 = os.path.join(management1.build_home, "project-func-info-databaase.json")
        func_file2 = os.path.join(management2.build_home, "project-func-info-databaase.json")
        report_file1 = os.path.join(management1.build_home, "report.txt")
        report_file2 = os.path.join(management2.build_home, "report.txt")
        
        analysis1 = LineAnalysis(func_file=func_file1, report_file=report_file1)  # type: ignore
        analysis2 = LineAnalysis(func_file=func_file2, report_file=report_file2)  # type: ignore
        result1 = analysis1.parse_report()
        result2 = analysis2.parse_report()
        if not result1 or not result2:
            return []
        for key in result1:
            modified_key = (key[0].replace(str(management1.build_home), str(management2.build_home)),) + tuple(key[1:])
            result1[key].extend(result2.get(modified_key, [0, 0]))
        result_list = []

        for key, value in result1.items():
            result_list.append(list(key) + value)
        return result_list


if __name__ == "__main__":
    programs = [
        "datamining/correlation",
        "datamining/covariance",
        "linear-algebra/blas/symm",
        "linear-algebra/kernels/2mm",
        "linear-algebra/kernels/3mm",
        "linear-algebra/solvers/cholesky",
        "linear-algebra/solvers/lu",
        "medley/nussinov",
        "stencils/heat-3d",
        "stencils/jacobi-2d",
        "linear-algebra/blas/gemm",
        "linear-algebra/blas/gemver",
        "linear-algebra/blas/gesummv",
        "stencils/seidel-2d",
        "linear-algebra/blas/syr2k",
        "linear-algebra/blas/syrk",
        "linear-algebra/blas/trmm",
        "linear-algebra/kernels/atax",
        "linear-algebra/kernels/bicg",
        "linear-algebra/kernels/doitgen",
        "linear-algebra/kernels/mvt",
        "linear-algebra/solvers/durbin",
        "linear-algebra/solvers/gramschmidt",
        "linear-algebra/solvers/ludcmp",
        "linear-algebra/solvers/trisolv",
        "medley/deriche",
        "medley/floyd-warshall",
        "stencils/adi",
        "stencils/fdtd-2d",
        "stencils/jacobi-1d",
    ]

    POLYBENCH_HOME = "/home/whq/dataset/polybench/polybench-instance1"
    too_fast_programs = [
        "linear-algebra/blas/gemver",  # 0.0190s
        "linear-algebra/blas/gesummv",  # 0.0120s
        "linear-algebra/kernels/atax",  # 0.0100s
        "linear-algebra/kernels/bicg",  # 0.0230s
        "linear-algebra/kernels/mvt",  # 0.0140s
        "linear-algebra/solvers/durbin",  # 0.0040s
        "linear-algebra/solvers/trisolv",  # 0.0050s
        "stencils/jacobi-1d",  # 0.0020s
        "medley/deriche",  # 0.1160s
    ]
    heavy_workload_programs = [
        "linear-algebra/solvers/cholesky",   # 9.6500s
        "linear-algebra/solvers/lu",         # 11.0730s
        "stencils/heat-3d",                 # 3.1630s
        "stencils/seidel-2d",               # 15.1250s
        "linear-algebra/solvers/ludcmp",     # 10.8250s
        "medley/floyd-warshall",             # 17.8080s
        "stencils/adi"                      # 9.3520s
    ]

    include_path = f"-I {POLYBENCH_HOME}/utilities -DLARGE_DATASET {POLYBENCH_HOME}/utilities/polybench.c"
    EXEC_TIME = ""
    for i in range(len(programs)):
        program = programs[i]
        if program not in too_fast_programs:
            include_path = (
                f"-I {POLYBENCH_HOME}/utilities {POLYBENCH_HOME}/utilities/polybench.c"
            )
        else:
            include_path = f"-DN=20000 -DM=20000 -DTSTEPS=20000 -I{POLYBENCH_HOME}/utilities {POLYBENCH_HOME}/utilities/polybench.c"
        cpu_core = 160 + i
        build_home = os.path.join(POLYBENCH_HOME, program)
        manager = PolybenchManager(
            build_home=build_home,
            cpucore=cpu_core,
            include_path=include_path,
            exec_param=str(EXEC_TIME),
            enable_gperftools=False,
        )
        manager.clean()
        print(f"Building {program}...")
        build_status = manager.build(str_opt_setting="")
        if build_status != 0:
            continue
        print(f"Testing the {i}-th program: {program}...", flush=True)
        exec_time = manager.test()
        if exec_time == FLOAT_MAX:
            print(f"Execution failed for {program}")
        else:
            print(f"Execution time for {program}: {exec_time:.4f} seconds")
        manager.clean()
