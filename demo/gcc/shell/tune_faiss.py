import json
import os
import multiprocessing
import random

from tuner import FlagInfo, Evaluator, FLOAT_MAX
from tuner import RandomTuner, SRTuner
from utils.constrain_solver import ConstrainsSolver
from manager.faiss_manager import FAISSManager

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
    search_space = dict() # pair: flag, configs
    # special case handling
    search_space["stdOptLv"] = GCCFlagInfo(name="stdOptLv", configs=[1,2,3], isParametric=True, stdOptLv=-1)
    with open(path, "r") as fp:
        stdOptLv = 0
        for raw_line in fp.read().split('\n'):
            # Process current chunk
            if(len(raw_line)):
                line = raw_line.replace(" ", "").strip()
                if line[0] != '#':
                    tokens = line.split("=")
                    flag_name = tokens[0]
                    # Binary flag
                    if len(tokens) == 1:
                        info = GCCFlagInfo(name=flag_name, configs=[False, True], isParametric=False, stdOptLv=stdOptLv)
                    # Parametric flag
                    else:
                        assert(len(tokens) == 2)
                        info = GCCFlagInfo(name=flag_name, configs=tokens[1].split(','), isParametric=True, stdOptLv=stdOptLv)
                    search_space[flag_name] = info
            # Move onto next chunk
            else:
                stdOptLv = stdOptLv+1
    return search_space


def convert_to_str(opt_setting, search_space):
    c = ConstrainsSolver(constrains_file="constrains/faiss.txt")
    c.solve(opt_config=opt_setting)
    str_opt_setting = " -O" + str(opt_setting["stdOptLv"])
    
    for flag_name, config in opt_setting.items():
        assert flag_name in search_space
        flag_info = search_space[flag_name]
        # Parametric flag
        if flag_info.isParametric:
            if flag_info.name != "stdOptLv" and len(config)>0:
                str_opt_setting += f" {flag_name}={config}"
        # Binary flag
        else:
            assert(isinstance(config, bool)), print(f"Expect {flag_name} to be bool, but got {type(config)}")
            if config:
                str_opt_setting += f" {flag_name}"
            else:
                negated_flag_name = flag_name.replace("-f", "-fno-", 1)
                str_opt_setting += f" {negated_flag_name}"
    return str_opt_setting


# Define tuning task
class FaissEvaluator(Evaluator):
    def __init__(self, path, num_repeats, search_space, manager, manager_base=None, enable_gperftools=False, artifact="a.out", result_file="faiss_data.json"):
        super().__init__(path, num_repeats)
        self.artifact = artifact
        self.manager = manager
        self.manager_base = manager_base
        self.enable_gperftools = enable_gperftools
        self.search_space = search_space
        self.result_file = result_file
    
    def build(self, str_opt_setting):
        str_opt_setting = str_opt_setting.replace("-O2", "-O3").replace("-O1", "-O3").replace("-Ofast", "-O3").replace("-Os", "-O3")
        print(f"Building with options: {str_opt_setting}", flush=True)
        info = self.manager.build(" -g" + str_opt_setting)
        if info != 0:
            return -1
        return 0
    
    
    def run(self, num_repeats):
        flag_time = self.manager.test(num_repeats)
        
        if flag_time == -1:
            print("Error in running the test task.")
            return -1
        return flag_time

    @staticmethod
    def _test_task(manager, num_repeats):
        return manager.test(num_repeats)

    def evaluate(self, opt_setting, num_repeats=-1):
        flags = convert_to_str(opt_setting, self.search_space)
        if "-O3" in flags and len(flags) < 50:
            return 1
        error = self.build(flags)
        if error == -1:
            return FLOAT_MAX

        if num_repeats == -1:
            num_repeats = self.num_repeats
        
        perf = self.run(num_repeats)
        if perf <= 0:
            return FLOAT_MAX
        
        # report_infos = FAISSManager.analysis_report(self.manager, self.manager_base)
        # with open(self.result_file, "a") as f:
        #     for report_info in report_infos:
        #         data = {"file_path": report_info[0], "function_name": report_info[1],
        #                 "begin_line": report_info[2], "end_line": report_info[3],
        #                 "opt_setting": opt_setting,
        #                 "self_time": report_info[4], "self_total_time": report_info[5],
        #                 "O3_self_time": report_info[6], "O3_total_time": report_info[7], "perf": perf}
        #         # print(data)
        #         f.write(json.dumps(data)+ '\n')
        self.clean()
        # We want to maximize the performance, so we return its inverse here.
        return 1 / perf


    def clean(self):
        self.manager.clean()


if __name__ == "__main__":
    budget = 500
    gcc_optimization_info = "opts_list/optimization_filter_v2.txt"

    search_space = read_gcc_opts(gcc_optimization_info)
    default_setting = {"stdOptLv":3}

    with open("tuning_faiss_Ofast_result.txt", "w") as ofp:
        ofp.write("=== Result ===\n")
        
    build_dir = "/home/whq/dataset/faiss/faiss"
    ann_dir = "/home/whq/dataset/faiss/ann-benchmarks-flags"
    env = "faiss"
    
    manager = FAISSManager(
        build_dir=build_dir,
        env=env,
        ann_dir=ann_dir,
        datasets=["sift-128-euclidean"],
        enable_gperftools=False,
    )
    
    evaluator = FaissEvaluator(path=None, num_repeats=1, manager=manager, manager_base=None, search_space=search_space)

    tuners = [
        SRTuner(search_space, evaluator, default_setting)
    ]

    for tuner in tuners:
        best_opt_setting, best_perf = tuner.tune(budget)
        if best_opt_setting is not None:
            default_perf = tuner.default_perf
            best_perf = evaluator.evaluate(best_opt_setting)
            print(f"Tuning Faiss w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x")
            with open(f"tune_result/Faiss_result.txt", "a") as ofp:
                ofp.write(f"Tuning Faiss w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x\n")
