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
    def __init__(self, path, num_repeats, search_space, manager, manager_base=None, enable_gperftools=False, artifact="a.out", result_file="tmp.json"):
        super().__init__(path, num_repeats)
        self.artifact = artifact
        self.manager = manager
        self.manager_base = manager_base
        self.enable_gperftools = enable_gperftools
        self.search_space = search_space
        self.result_file = result_file
    
    # def build(self, str_opt_setting):
    #     info = self.manager.build(" -g" + str_opt_setting)
    #     if info == -1:
    #         with open ("opts_list/faiss_bad_config.txt", "a") as ofp:
    #             ofp.write(f"{str_opt_setting}\n")
    #         return -1
    #     self.manager_base.build(" -g -O3")
    #     return 0
    
    @staticmethod
    def _build_task(manager, build_options):
        return manager.build(build_options)
    
    def build(self, str_opt_setting):
        print(f"Building with options: {str_opt_setting}", flush=True)
        args_list = [
            (self.manager, " -g " + str_opt_setting),
            (self.manager_base, " -g -O3")
        ]
        
        with multiprocessing.Pool(processes=2) as pool:
            results = pool.starmap(self._build_task, args_list)
        
        if any(result == -1 for result in results):
            print(f"Build failed for the configuration: {str_opt_setting}")
        return 0

    # def run(self, num_repeats):
    #     flag_time = self.manager.test(num_repeats)
    #     if flag_time == -1:
    #         return -1
    #     base_time = self.manager_base.test(num_repeats)
    #     return flag_time / base_time
    
    def run(self, num_repeats):
        args_list = [
            (self.manager, num_repeats),
            (self.manager_base, num_repeats)
        ]
        
        with multiprocessing.Pool(processes=2) as pool:
            results = pool.starmap(self._test_task, args_list)
        
        flag_time, base_time = results
        if flag_time == -1 or base_time == -1:
            print("Error in running the test task.")
            return -1
        return FAISSManager.caculate_qps_acc(flag_time, base_time)

    @staticmethod
    def _test_task(manager, num_repeats):
        return manager.test(num_repeats)

    def evaluate(self, opt_setting, num_repeats=-1):
        flags = convert_to_str(opt_setting, self.search_space)
        if "-O3" in flags and len(flags) < 50:
            return 1
        with open("scann_tmp.txt", "a") as f:
            f.write(f"\'{flags}\',\n")
        return FLOAT_MAX
        error = self.build(flags)
        if error == -1:
            return FLOAT_MAX

        if num_repeats == -1:
            num_repeats = self.num_repeats
        
        perf = self.run(num_repeats)
        if perf <= 0:
            return FLOAT_MAX
        
        report_infos = FAISSManager.analysis_report(self.manager, self.manager_base)
        self.clean()
        return 1 / perf


    def clean(self):
        self.manager.clean()
        self.manager_base.clean()


if __name__ == "__main__":
    budget = 300
    gcc_optimization_info = "opts_list/gcc_for_openEuler_ofast.txt"

    search_space = read_gcc_opts(gcc_optimization_info)
    default_setting = {"stdOptLv":3}
    
    evaluator = FaissEvaluator(path=None, num_repeats=1, manager=None, manager_base=None, search_space=search_space)

    tuners = [
        SRTuner(search_space, evaluator, default_setting)
    ]

    for tuner in tuners:
        best_opt_setting, best_perf = tuner.tune(budget)
        if best_opt_setting is not None:
            default_perf = tuner.default_perf
            best_perf = evaluator.evaluate(best_opt_setting)
            print(f"Tuning MySQL w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x")
            with open(f"tune_result/anc_result.txt", "a") as ofp:
                ofp.write(f"Tuning MySQL w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x\n")
