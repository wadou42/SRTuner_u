import json
import os
import multiprocessing

from tuner import FlagInfo, Evaluator, FLOAT_MAX
from tuner import RandomTuner, SRTuner
from manager.mysql_manager import MySQLManager
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
    c = ConstrainsSolver(constrains_file="constrains/cbench.txt")
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
            assert(isinstance(config, bool))
            if config:
                str_opt_setting += f" {flag_name}"
            else:
                negated_flag_name = flag_name.replace("-f", "-fno-", 1)
                str_opt_setting += f" {negated_flag_name}"
    return str_opt_setting


# Define tuning task
class cBenchEvaluator(Evaluator):
    def __init__(self, path, num_repeats, search_space, manager, manager_base=None, enable_gperftools=False, artifact="a.out", result_file="mysql.json"):
        super().__init__(path, num_repeats)
        self.artifact = artifact
        self.manager = manager
        self.manager_base = manager_base
        self.enable_gperftools = enable_gperftools
        self.search_space = search_space
        self.result_file = result_file

    @staticmethod
    def _build_task(manager, build_options):
        return manager.build(build_options)

    def build(self, str_opt_setting):
        str_opt_setting = " -O3"
        args_list = [
            (self.manager, " -g " + str_opt_setting),
            # (self.manager_base, " -g -O3")
        ]
        
        with multiprocessing.Pool(processes=2) as pool:
            results = pool.starmap(self._build_task, args_list)
        
        if any(result == -1 for result in results):
            return -1
        return 0
    
    # def build(self, str_opt_setting):
    #     info = self.manager.build(" -g" + str_opt_setting)
    #     if info == -1:
    #         return -1
    #     self.manager_base.build(" -g -O3")
    #     return 0

    def run(self, num_repeats):
        flag_time = 0
        base_time = 0
        for i in range(num_repeats):
            flag_time += float(self.manager.test())
            # base_time += float(self.manager_base.test())
            base_time = 1
        return flag_time / base_time

    def evaluate(self, opt_setting, num_repeats=-1):
        flags = convert_to_str(opt_setting, self.search_space)
        if "-O3" in flags and len(flags) < 20:
            return 1
        error = self.build(flags)
        if error == -1:
            return FLOAT_MAX

        if num_repeats == -1:
            num_repeats = self.num_repeats
        
        perf = self.run(num_repeats)
        if perf < 0:
            return FLOAT_MAX
        
        report_infos = MySQLManager.analysis_report(self.manager, self.manager_base)
        with open(self.result_file, "a") as f:
            for report_info in report_infos:
                data = {"file_path": report_info[0], "function_name": report_info[1],
                        "begin_line": report_info[2], "end_line": report_info[3],
                        "opt_setting": opt_setting,
                        "self_time": report_info[4], "self_total_time": report_info[5], 
                        "O3_self_time": report_info[6], "O3_total_time": report_info[7], "perf": perf}
                # print(data)
                f.write(json.dumps(data)+ '\n')
    
        # self.clean()
        return perf


    def clean(self):
        self.manager.clean()
        self.manager_base.clean()


if __name__ == "__main__":
    # budget = 300
    budget = 2

    gcc_optimization_info = "opts_list/gcc_for_openEuler_ofast.txt"

    search_space = read_gcc_opts(gcc_optimization_info)
    default_setting = {"stdOptLv":3}

    with open("tuning_result.txt", "w") as ofp:
        ofp.write("=== Result ===\n")
        
    build_home = "/home/whq/dataset/mysql/mysql/build"
    install_home = "/home/whq/bin/mysql"
    data_home = "/home/whq/data/mysql"
    test_home = "/home/whq/dataset/mysql/sysbench"
    cnf_file = "/home/whq/dataset/mysql/mysql.cnf"

    build_home_base = "/home/whq/dataset/mysql/mysqlO3/build"
    install_home_base = "/home/whq/bin/mysqlO3"
    data_home_base = "/home/whq/data/mysqlO3"
    test_home_base = "/home/whq/dataset/mysql/sysbench"
    cnf_file_base = "/home/whq/dataset/mysql/mysqlO3.cnf"
    
    manager =  MySQLManager(build_home=build_home,
                               install_home=install_home,
                               data_home=data_home,
                               test_home=test_home,
                               cnf_file=cnf_file)
    
    manager_base =  MySQLManager(build_home=build_home_base,
                               install_home=install_home_base,
                               data_home=data_home_base,
                               test_home=test_home_base,
                               cnf_file=cnf_file_base)
    
    evaluator = cBenchEvaluator(path=None, num_repeats=1, manager=manager, manager_base=manager_base, search_space=search_space)

    tuners = [
        SRTuner(search_space, evaluator, default_setting)
    ]

    for tuner in tuners:
        best_opt_setting, best_perf = tuner.tune(budget)
        if best_opt_setting is not None:
            default_perf = tuner.default_perf
            best_perf = evaluator.evaluate(best_opt_setting)
            print(f"Tuning MySQL w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x")
            with open(f"tune_result/MySQL_result.txt", "a") as ofp:
                ofp.write(f"Tuning MySQL w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x\n")
