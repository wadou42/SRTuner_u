import json
import os
import queue
import threading
import traceback

from tuner import FlagInfo, Evaluator, FLOAT_MAX
from tuner import RandomTuner, SRTuner
from manager.cBench_manager import cBenchManager
from utils.reduce_constrain.constrain_solver import ConstrainsSolver
from utils.reduce_constrain.reduce_constrain import OptReducer
from utils.thread_log import ThreadTeeLogger

NUM_SLOTS = 8
CORES_PER_SLOT = 40

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


def convert_to_str(opt_setting, search_space, constrains_file=None):
    if constrains_file is not None:
        c = ConstrainsSolver(constrains_file=constrains_file)
        c.solve(opt_config=opt_setting)

    # str_opt_setting = " -O" + str(opt_setting["stdOptLv"])
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
        num_repeats,
        search_space,
        manager,
        manager_base,
        constraints_file,
        artifact="a.out",
        retest_O3=False,
    ):
        super().__init__(num_repeats)
        self.artifact = artifact
        self.manager = manager
        self.manager_base = manager_base
        self.constraints_file = constraints_file
        
        self.search_space = search_space
        self.retest_O3 = retest_O3
        self.O3_perf = self._init_o3_perf()
        self.optReducer:OptReducer = self._init_opt_reducer()

    def _init_o3_perf(self):
        if self.retest_O3:
            return 1
        else:
            self.manager_base.build(" -g -O3")
            return self.manager_base.test(input_id=1, num_repeats=self.num_repeats)

    def _init_opt_reducer(self):
        return OptReducer(
            manager = self.manager,
            constrains_file = self.constraints_file,
            num_repeats=self.num_repeats,
            opt_file = "opts_list/gcc_O2_O3_others.txt",
            O3_perf = self.O3_perf,
            min_perf= self.O3_perf / 10,
            max_perf= self.O3_perf * 1.005,
            verbose = False,
            print_build_result = True,
        )


    def build(self, str_opt_setting):
        if self.retest_O3:
            self.manager_base.build(" -O3")

        return self.manager.build(str_opt_setting)

    def run(self, num_repeats, input_id=1):
        if self.retest_O3:
            flag_time = 0
            base_time = 0
            for i in range(num_repeats):
                flag_time += self.manager.test(input_id)
                base_time += self.manager_base.test(input_id)
            self.O3_perf = base_time / num_repeats
            perf = flag_time / base_time
            return perf
        if self.O3_perf is None:
            self.O3_perf = self.manager_base.test(input_id=1, num_repeats=num_repeats)
        
        flag_time = self.manager.test(input_id, num_repeats=num_repeats)

        return flag_time / self.O3_perf

    def evaluate(self, opt_setting, num_repeats=-1)->tuple[int, float]:
        cost = 1
        flags = convert_to_str(opt_setting, self.search_space, self.constraints_file)
        error = self.build(flags)
        if error == -1:
            return (cost, FLOAT_MAX)

        if num_repeats == -1:
            num_repeats = self.num_repeats
        perf = self.run(num_repeats, input_id=1)
        
        # If the performance is worse than O3, we consider it as a failed case and reduce the flags.
        if perf > 1.03:
            self.optReducer.reduce_config_until_pass(flags)
            cost = self.optReducer.last_reduce_config_build_count
        print(f"Evaluated config: {flags}, perf: {perf:.3f}, cost: {cost}")
        self.clean()
        return (cost, perf)

    def clean(self):
        self.manager.clean()
        if self.retest_O3:
            self.manager_base.clean()


def tune_benchmark(benchmark, slot_id):
    log_file = os.path.join("tune_result", "logs", f"{benchmark}.log")
    with ThreadTeeLogger(log_file, mode="w"):
        try:
            return _tune_benchmark(benchmark, slot_id)
        except Exception:
            print(traceback.format_exc(), flush=True)
            raise


def _tune_benchmark(benchmark, slot_id):
    slot_first_core = slot_id * CORES_PER_SLOT
    slot_last_core = slot_first_core + CORES_PER_SLOT - 1
    slot_cores = f"{slot_first_core}-{slot_last_core}"
    thread_name = threading.current_thread().name
    print(
        f"[{thread_name}] Start tuning {benchmark} on slot {slot_id} "
        f"(cores {slot_cores}, cbench core {slot_first_core})",
        flush=True,
    )

    path_flag = os.path.join(benchmark_home, "cBench-instance0", benchmark, "src")
    path_base = os.path.join(benchmark_home, "cBench-instance1", benchmark, "src")

    path_flag = os.path.join(benchmark_home, "cbench-instance0", benchmark, "src")
    path_base = os.path.join(benchmark_home, "cbench-instance1", benchmark, "src")

    manager = cBenchManager(
        path_flag,
        "a.out",
        cpucore=slot_first_core,
        build_cpucore=slot_cores,
    )
    manager_base = cBenchManager(
        path_base,
        "a.out",
        cpucore=slot_first_core,
        build_cpucore=slot_cores,
    )

    tier_heavy = ["security_rijndael_e"]

    tier_medium_heavy = [
        "security_blowfish_e",
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

    # if benchmark in tier_heavy:
    #     num_repeats = 10
    # elif benchmark in tier_medium_heavy:
    #     num_repeats = 15
    # elif benchmark in tier_medium:
    #     num_repeats = 24
    # elif benchmark in tier_light:
    #     num_repeats = 30
    # else:
    #     assert False, f"Unknown benchmark tier: {benchmark}"

    num_repeats = 10

    evaluator = cBenchEvaluator(
        num_repeats=num_repeats,
        search_space=search_space,
        manager=manager,
        manager_base=manager_base,
        constraints_file=f"constrains/{benchmark}_constrains.txt",
    )

    result_lines = []
    for tuner in [SRTuner(search_space, evaluator, default_setting)]:
        best_opt_setting, best_perf = tuner.tune(budget)
        if best_opt_setting is not None:
            default_perf = tuner.default_perf
            best_perf = evaluator.evaluate(best_opt_setting)[1]
            line = f"Tuning {benchmark} w/ {tuner.name}: {default_perf:.3f}/{best_perf:.3f} = {default_perf/best_perf:.3f}x"
            print(line)
            with open(f"tune_result/{benchmark}_result.txt", "a") as ofp:
                ofp.write(line + "\n")
            result_lines.append(line)
    return result_lines


def tune_worker(slot_id, benchmark_queue, all_results, errors):
    while True:
        item = benchmark_queue.get()
        if item is None:
            benchmark_queue.task_done()
            break

        index, benchmark = item
        try:
            all_results[index] = tune_benchmark(benchmark, slot_id)
        except Exception as exc:
            errors.append((benchmark, exc))
        finally:
            benchmark_queue.task_done()


if __name__ == "__main__":
    budget = 1000
    benchmark_home = "/home/whq/dataset/cbench"
    benchmark_list = [
            "consumer_jpeg_d","security_sha","automotive_susan_e","consumer_lame","consumer_mad",
            "security_rijndael_d","automotive_susan_c", "telecom_adpcm_d", "security_blowfish_d",
            "telecom_adpcm_c", "network_dijkstra", "telecom_CRC32",
            "automotive_bitcount", "consumer_tiff2bw",  "consumer_tiffdither",
            "consumer_tiffmedian", "automotive_susan_s",  "consumer_tiff2rgba", "network_patricia",
            "consumer_jpeg_c",  "office_rsynth", "security_rijndael_e",
            "office_stringsearch1", "bzip2e",   "security_blowfish_e", "bzip2d",
               "telecom_gsm",
            "automotive_qsort1"
        ]
    

    gcc_optimization_info = "opts_list/gcc_O2_O3_others.txt"
    search_space = read_gcc_opts(gcc_optimization_info)
    default_setting = {"stdOptLv": 3}

    os.makedirs("tune_result", exist_ok=True)
    os.makedirs(os.path.join("tune_result", "logs"), exist_ok=True)

    with open("tuning_result.txt", "w") as ofp:
        ofp.write("=== Result ===\n")

    benchmark_queue = queue.Queue()
    all_results = [None] * len(benchmark_list)
    errors = []

    for index, benchmark in enumerate(benchmark_list):
        benchmark_queue.put((index, benchmark))

    workers = []
    for slot_id in range(NUM_SLOTS):
        worker = threading.Thread(
            target=tune_worker,
            args=(slot_id, benchmark_queue, all_results, errors),
            name=f"gcc-slot-{slot_id}",
        )
        worker.start()
        workers.append(worker)

    for _ in workers:
        benchmark_queue.put(None)

    benchmark_queue.join()
    for worker in workers:
        worker.join()

    if errors:
        benchmark, exc = errors[0]
        raise RuntimeError(f"Tuning failed for {benchmark}") from exc

    # 汇总所有 benchmark 的结果
    with open("tuning_gcc_result.txt", "a") as ofp:
        for result in all_results:
            if result is None:
                continue
            for line in result:
                ofp.write(line + "\n")
