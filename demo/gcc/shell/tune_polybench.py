import json
import os
import multiprocessing

from tuner import FlagInfo, Evaluator, FLOAT_MAX
from tuner import RandomTuner, SRTuner
from manager.polybench_manager import PolybenchManager
from utils.constrain_solver import ConstrainsSolver


# Define GCC flags
class GCCFlagInfo(FlagInfo):
    def __init__(self, name, configs, isParametric, stdOptLv):
        super().__init__(name, configs)
        self.isParametric = isParametric
        self.stdOptLv = stdOptLv


# Read the list of gcc optimizations that follows certain format.
# Due to a slight difference in GCC distributions, the supported flags are confirmed by using -fverbose-asm.
# Each chunk specifies flags supported under each standard optimization levels.
# Besides flags identified by -fverbose-asm, we also considered flags in online doc.
# They are placed as the last chunk and considered as last optimization level.
# (any standard optimization level would not configure them.)
def read_gcc_opts(path):
    search_space = dict()  # pair: flag, configs
    # special case handling
    search_space["stdOptLv"] = GCCFlagInfo(
        name="stdOptLv", configs=[1, 2, 3], isParametric=True, stdOptLv=-1
    )
    with open(path, "r") as fp:
        stdOptLv = 0
        for raw_line in fp.read().split("\n"):
            # Process current chunk
            if len(raw_line):
                line = raw_line.replace(" ", "").strip()
                if line[0] != "#":
                    tokens = line.split("=")
                    flag_name = tokens[0]
                    # Binary flag
                    if len(tokens) == 1:
                        info = GCCFlagInfo(
                            name=flag_name,
                            configs=[False, True],
                            isParametric=False,
                            stdOptLv=stdOptLv,
                        )
                    # Parametric flag
                    else:
                        assert len(tokens) == 2
                        info = GCCFlagInfo(
                            name=flag_name,
                            configs=tokens[1].split(","),
                            isParametric=True,
                            stdOptLv=stdOptLv,
                        )
                    search_space[flag_name] = info
            # Move onto next chunk
            else:
                stdOptLv = stdOptLv + 1
    return search_space


def convert_to_str(opt_setting, search_space):
    c = ConstrainsSolver(constrains_file="constrains/polybench.txt")
    c.solve(opt_config=opt_setting)

    str_opt_setting = " -O" + str(opt_setting["stdOptLv"])
    str_opt_setting = " -O3 "
    for flag_name, config in opt_setting.items():
        assert flag_name in search_space
        flag_info = search_space[flag_name]
        # Parametric flag
        if flag_info.isParametric:
            if flag_info.name != "stdOptLv" and len(config) > 0:
                str_opt_setting += f" {flag_name}={config}"
        # Binary flag
        else:
            assert isinstance(config, bool)
            if config:
                str_opt_setting += f" {flag_name}"
            else:
                negated_flag_name = flag_name.replace("-f", "-fno-", 1)
                str_opt_setting += f" {negated_flag_name}"
    return str_opt_setting


# Define tuning task
class PolybenchEvaluator(Evaluator):
    def __init__(
        self,
        path,
        num_repeats,
        search_space,
        manager,
        manager_base,
        enable_gperftools=False,
        artifact="a.out",
        result_file="report.json",
    ):
        super().__init__(path, num_repeats)
        self.artifact = artifact
        self.manager = manager
        self.manager_base = manager_base
        self.enable_gperftools = enable_gperftools
        self.search_space = search_space
        self.result_file = result_file

    def build(self, str_opt_setting): # type: ignore
        info = self.manager.build(" -g" + str_opt_setting)
        if info == -1:
            return -1
        self.manager_base.build(" -g -O3")
        return 0

    def run(self, num_repeats, input_id=1): # type: ignore
        flag_time = 0
        base_time = 0
        for i in range(num_repeats):
            flag_time += self.manager.test()
            base_time += self.manager_base.test()
        return flag_time / base_time

    def evaluate(self, opt_setting, num_repeats=-1): # type: ignore
        self.clean()
        flags = convert_to_str(opt_setting, self.search_space)
        if "-O3" in flags and len(flags) < 20:
            return 1
        error = self.build(flags)
        if error == -1:
            return FLOAT_MAX

        if num_repeats == -1:
            num_repeats = self.num_repeats
        perf = self.run(num_repeats, input_id=1)

        report_infos = PolybenchManager.analysis_report(self.manager, self.manager_base)
        with open(self.result_file, "a") as f:
            for report_info in report_infos:
                data = {
                    "file_path": report_info[0],
                    "function_name": report_info[1],
                    "begin_line": report_info[2],
                    "end_line": report_info[3],
                    "opt_setting": opt_setting,
                    "self_time": report_info[4],
                    "self_total_time": report_info[5],
                    "O3_self_time": report_info[6],
                    "O3_total_time": report_info[7],
                    "perf": perf,
                }
                # print(data)
                f.write(json.dumps(data) + "\n")

        self.clean()
        return perf

    def clean(self):
        self.manager.clean()
        self.manager_base.clean()


def tune_benchmark(benchmark):
    build_home_flag = os.path.join(benchmark_home, "polybench-instance0", benchmark)
    build_home_base = os.path.join(benchmark_home, "polybench-instance1", benchmark)

    assigned_core = 0 + multiprocessing.current_process()._identity[0]

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
    
    POLYBENCH_HOME_FLAG = "/home/whq/dataset/polybench/polybench-instance0"
    POLYBENCH_HOME_BASE = "/home/whq/dataset/polybench/polybench-instance1"
    if benchmark not in too_fast_programs:
        
        include_path_base = (
            f"-I {POLYBENCH_HOME_BASE}/utilities {POLYBENCH_HOME_BASE}/utilities/polybench.c"
        )
        include_path_flag = (
            f"-I {POLYBENCH_HOME_FLAG}/utilities {POLYBENCH_HOME_FLAG}/utilities/polybench.c"
        )
    else:
        include_path_base = f"-DN=20000 -DM=20000 -DTSTEPS=20000 -I {POLYBENCH_HOME_BASE}/utilities {POLYBENCH_HOME_BASE}/utilities/polybench.c"
        include_path_flag = f"-DN=20000 -DM=20000 -DTSTEPS=20000 -I {POLYBENCH_HOME_FLAG}/utilities {POLYBENCH_HOME_FLAG}/utilities/polybench.c"

    benchmark_config = {
        # 超重型 (耗时长，减少次数)
        "medley/floyd-warshall": 3,      # ~53s
        "stencils/seidel-2d": 3,         # ~45s
        "linear-algebra/solvers/lu": 4,  # ~44s
        "linear-algebra/solvers/ludcmp": 4, # ~43s
        
        # 重型
        "linear-algebra/solvers/cholesky": 5, # ~48s
        "stencils/adi": 5,               # ~46s
        "stencils/heat-3d": 18,          # ~38s
        
        # 中型
        "medley/nussinov": 15,           # ~43s
        "linear-algebra/kernels/3mm": 15, # ~43s
        "linear-algebra/blas/gemver": 20, # ~48s
        "linear-algebra/blas/gesummv": 30, # ~47s
        "linear-algebra/kernels/bicg": 40, # ~43s
        "linear-algebra/kernels/mvt": 25,  # ~46s
        "stencils/jacobi-2d": 40,        # ~43s
        "linear-algebra/kernels/2mm": 25,  # ~43s
        "linear-algebra/solvers/gramschmidt": 25, # ~41s
        "stencils/fdtd-2d": 40,          # ~41s
        "linear-algebra/blas/syr2k": 30,  # ~42s
        "linear-algebra/blas/symm": 35,   # ~41s
        "datamining/covariance": 35,      # ~41s
        "datamining/correlation": 70,     # ~44s
        
        # 轻型
        "linear-algebra/blas/gemm": 50,   # ~43s
        "linear-algebra/kernels/atax": 50, # ~43s
        "linear-algebra/blas/syrk": 65,   # ~43s
        "linear-algebra/blas/trmm": 75,   # ~44s
        "stencils/jacobi-1d": 80,         # ~44s
        
        # 微型 (耗时极短，大幅增加次数以确保测量精度)
        "linear-algebra/kernels/doitgen": 100, # ~43s
        "linear-algebra/solvers/trisolv": 100, # ~46s
        "linear-algebra/solvers/durbin": 100,  # ~46s
        "medley/deriche": 200            # ~45s
    }

    if benchmark in benchmark_config:
        num_repeats = int(benchmark_config[benchmark] / 2) + 1
    else:
        assert False, f"Unknown benchmark tier: {benchmark}"
    manager_base = PolybenchManager(
            build_home=build_home_base,
            cpucore=assigned_core,
            include_path=include_path_base,
            num_repeats=num_repeats,
            enable_gperftools=True
        )
    manager_flag = PolybenchManager(
            build_home=build_home_flag,
            cpucore=assigned_core,
            include_path=include_path_flag,
            num_repeats=num_repeats,
            enable_gperftools=True
        )
    
    result_file = os.path.join("tune_result_polybench", f"{benchmark.split('/')[-1]}.jsonl")
        
    evaluator = PolybenchEvaluator(
        path=None,
        num_repeats=1,
        manager=manager_flag,
        manager_base=manager_base,
        search_space=search_space,
        result_file=result_file,
    )

    result_lines = []
    for tuner in [RandomTuner(search_space, evaluator, default_setting)]:
        best_opt_setting, best_perf = tuner.tune(budget)
        if best_opt_setting is not None:
            default_perf = tuner.default_perf
            best_perf = evaluator.evaluate(best_opt_setting)
            line = f"Tuning {benchmark} w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x"
            print(line)
            with open(f"tune_result/{benchmark}_result.txt", "a") as ofp:
                ofp.write(line + "\n")
            result_lines.append(line)
    return result_lines


if __name__ == "__main__":
    budget = 1
    benchmark_home = "/home/whq/dataset/polybench"
    benchmark_list = [
        # "datamining/correlation",
        # "datamining/covariance",
        # "linear-algebra/blas/symm",
        # "linear-algebra/kernels/2mm",
        # "linear-algebra/kernels/3mm",
        # "linear-algebra/solvers/cholesky",
        # "linear-algebra/solvers/lu",
        # "medley/nussinov",
        # "stencils/heat-3d",
        # "stencils/jacobi-2d",
        # "linear-algebra/blas/gemm",
        # "linear-algebra/blas/gemver",
        # "linear-algebra/blas/gesummv",
        # "stencils/seidel-2d",
        # "linear-algebra/blas/syr2k",
        # "linear-algebra/blas/syrk",
        # "linear-algebra/blas/trmm",
        # "linear-algebra/kernels/atax",
        # "linear-algebra/kernels/bicg",
        # "linear-algebra/kernels/doitgen",
        # "linear-algebra/kernels/mvt",
        # "linear-algebra/solvers/durbin",
        # "linear-algebra/solvers/gramschmidt",
        # "linear-algebra/solvers/ludcmp",
        # "linear-algebra/solvers/trisolv",
        # "medley/deriche",
        # "medley/floyd-warshall",
        # "stencils/adi",
        # "stencils/fdtd-2d",
        "stencils/jacobi-1d",
    ]

    gcc_optimization_info = "opts_list/gcc_O2_O3_others.txt"
    search_space = read_gcc_opts(gcc_optimization_info)
    default_setting = {"stdOptLv": 3}

    os.makedirs("tune_result", exist_ok=True)

    with open("tuning_result.txt", "w") as ofp:
        ofp.write("=== Result ===\n")

    with multiprocessing.Pool(processes=min(36, len(benchmark_list))) as pool:
        all_results = pool.map(tune_benchmark, benchmark_list)

    with open("tuning_polybench_with_ofast_result.txt", "a") as ofp:
        for result in all_results:
            for line in result:
                ofp.write(line + "\n")
