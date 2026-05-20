import json
import os
import multiprocessing

from tuner import FlagInfo, Evaluator, FLOAT_MAX
from tuner import RandomTuner, SRTuner
from manager.cBench_manager import cBenchManager
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
    c = ConstrainsSolver(constrains_file="constrains/cbench.txt")
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
class cBenchEvaluator(Evaluator):
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

    def build(self, str_opt_setting): #type: ignore
        info = self.manager.build(" -g" + str_opt_setting)
        if info == -1:
            return -1
        self.manager_base.build(" -g -O3") #type: ignore
        return 0

    def run(self, num_repeats, input_id=1): #type: ignore
        flag_time = 0
        base_time = 0
        for i in range(num_repeats):
            flag_time += self.manager.test(input_id)
            base_time += self.manager_base.test(input_id)
        return flag_time / base_time

    def evaluate(self, opt_setting, num_repeats=-1): #type: ignore
        flags = convert_to_str(opt_setting, self.search_space)
        if "-O3" in flags and len(flags) < 20:
            return 1
        error = self.build(flags)
        if error == -1:
            return FLOAT_MAX

        if num_repeats == -1:
            num_repeats = self.num_repeats

        perf = self.run(num_repeats, input_id=1)

        report_infos = cBenchManager.analysis_report(self.manager, self.manager_base)
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
    path_flag = os.path.join(benchmark_home, "cBench", benchmark, "src")
    path_base = os.path.join(benchmark_home, "cBenchO3", benchmark, "src")

    worker_id = 32 + multiprocessing.current_process()._identity[0]

    total_cores = multiprocessing.cpu_count()
    assigned_core = worker_id % total_cores

    path_flag = os.path.join(benchmark_home, "cbench-instance0", benchmark, "src")
    path_base = os.path.join(benchmark_home, "cbench-instance1", benchmark, "src")

    manager = cBenchManager(path_flag, "a.out", cpucore=assigned_core)
    manager_base = cBenchManager(path_base, "a.out", cpucore=assigned_core)

    result_file = os.path.join("tune_result", f"{benchmark}.jsonl")

    tier_heavy = ["security_rijndael_e"]

    tier_medium_heavy = [
        "security_blowfish_d",
        "security_sha",
        "consumer_tiff2rgba",
        "consumer_jpeg_d",
    ]

    tier_medium = [
        "consumer_tiffdither",
        "consumer_jpeg_c",
        "consumer_tiff2bw",
        "bzip2d",
    ]

    tier_light = [
        "consumer_tiffmedian",
        "bzip2e",
        "telecom_adpcm_d",
        "automotive_qsort1",
        "telecom_gsm",
        "automotive_susan_c",
        "automotive_susan_e",
        "telecom_CRC32",
        "telecom_adpcm_c",
        "automotive_susan_s",
        "automotive_bitcount",
        "office_stringsearch1",
        "office_rsynth",
        "network_patricia",
        "network_dijkstra",
    ]

    if benchmark in tier_heavy:
        num_repeats = 10
    elif benchmark in tier_medium_heavy:
        num_repeats = 15
    elif benchmark in tier_medium:
        num_repeats = 24
    elif benchmark in tier_light:
        num_repeats = 30
    else:
        assert False, f"Unknown benchmark tier: {benchmark}"

    evaluator = cBenchEvaluator(
        path=None,
        num_repeats=num_repeats,
        manager=manager,
        manager_base=manager_base,
        search_space=search_space,
        result_file=result_file,
    )

    result_lines = []
    for tuner in [SRTuner(search_space, evaluator, default_setting)]:
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

from argparse import ArgumentParser
if __name__ == "__main__":
    benchmark_home = "/home/whq/dataset/cBench"
    benchmark_list = [
            "automotive_bitcount", "consumer_tiff2bw", "automotive_susan_e", "consumer_tiffdither",
            "automotive_susan_c",
            "consumer_tiffmedian", "automotive_susan_s", "network_dijkstra", "consumer_tiff2rgba", "network_patricia",
            "consumer_jpeg_c", "security_rijndael_d", "office_rsynth", "security_rijndael_e", "security_sha",
            "office_stringsearch1", "bzip2e", "security_blowfish_d", "telecom_adpcm_c", "security_blowfish_e", "bzip2d",
            "telecom_adpcm_d", "consumer_jpeg_d", "telecom_CRC32", "consumer_lame", "telecom_gsm", "consumer_mad",
            "automotive_qsort1"
        ]

    budget = 25
    benchmark_home = "/home/whq/dataset/cBench"
   
    gcc_optimization_info = "opts_list/gcc_O2_O3_others.txt"
    search_space = read_gcc_opts(gcc_optimization_info)
    default_setting = {"stdOptLv": 3}
    
    parser = ArgumentParser()
    parser.add_argument("--num", type=int, default=1, help="number of parallel processes")
    args = parser.parse_args()
    idx = args.num
    
    build_home_flag = os.path.join(benchmark_home, f"cbench-instance{2*idx}", "security_blowfish_e", "src")
    build_home_base = os.path.join(benchmark_home, f"cbench-instance{2*idx+1}", "security_blowfish_e", "src")

    assigned_core = 160 + idx
   

    num_repeats = 15
    manager_base = cBenchManager(
        path=build_home_base,
        artifact="a.out",
        cpucore=assigned_core
    )
    
    manager_flag = cBenchManager(
        path=build_home_flag,
        artifact="a.out",
        cpucore=assigned_core
    )
    
    result_file = os.path.join("tune_result/security_blowfish_e", f"{idx}_result.json")
        
    evaluator = cBenchEvaluator(
        path=None,
        num_repeats=10,
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
            line = f"Tuning security_blowfish_e w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x"
            print(line)
            with open(f"tune_result/security_blowfish_e.txt", "a") as ofp:
                ofp.write(line + "\n")



