import random
import argparse
import os

from utils.reduce_constrain.constrain_solver import ConstrainsSolver

class OptReducer:
    def __init__(
        self,
        manager,
        constrains_file: str,
        opt_file: str,
        num_repeats: int = 20,
        provided_configs: list[str] | None = None,
        random_config_count: int = 20,
        min_perf: float = 1e-3,
        max_perf: float = 1e6,
        pass_ratio: float = 0.9,
        run_tests: bool = True,
        invert_perf_result: bool = False,
        O3_perf: float | None = None,
        verbose: bool = True,
        print_build_result: bool = False,
    ):
        self.manager = manager
        self.constrains_file = constrains_file
        self.opt_file = opt_file
        self.num_repeats = num_repeats
        self.provided_configs = provided_configs
        self.random_config_count = random_config_count
        self.min_perf = min_perf
        self.max_perf = max_perf
        self.pass_ratio = pass_ratio
        self.run_tests = run_tests
        self.invert_perf_result = invert_perf_result
        self.O3_perf = O3_perf
        self.verbose = verbose
        self.print_build_result = print_build_result
        self.build_and_test_count = 0
        self.last_reduce_config_build_count = 0
        self.optlist = self._load_opts()

    def _log(self, *args, **kwargs) -> None:
        if self.verbose:
            print(*args, **kwargs)

    def _format_build_result(self, opt_config: str, result: float | int) -> None:
        if self.print_build_result:
            print(
                f"[build_and_test] flag: {opt_config} result: {result}",
                flush=True,
            )

    def _load_opts(self):
        with open(self.opt_file, "r") as f:
            opts = [
                line.strip() for line in f if line.strip() and not line.startswith("#")
            ]
        opts = sorted(set(opts))
        return opts

    @staticmethod
    def get_opt_negation(option: str) -> str:
        option = option.strip()
        if not option:
            return ""
        
        if '=' in option:
            return ''
            

        # Define common negation rules
        # Note: Longer prefixes (like "-fno-") must be checked before shorter ones (like "-f")
        negation_rules = [
            ("-fno-", "-f"),
            ("-f", "-fno-"),
            ("-mno-", "-m"),
            ("-m", "-mno-"),
            ("-Wno-", "-W"),
            ("-W", "-Wno-"),
        ]

        for prefix, negated_prefix in negation_rules:
            if option.startswith(prefix):
                return negated_prefix + option[len(prefix) :]
        return ""

    def random_opts(self, n: int = -1) -> list[str]:
        """
        Randomly generate n compiler option configurations from optlist.
        Each configuration will include -O3 and a random subset of options.

        param n: Number of random configurations to generate (default: 10)
        return: List of option config strings like ["-O3 -opt1 -opt2", "-O3 -opt3"]
        """

        # If provided_configs is set, use it directly.
        if self.provided_configs is not None:
            return self.provided_configs

        # If optlist is not loaded properly, return an empty list.
        if not hasattr(self, "optlist") or not self.optlist:
            return []

        # Generate random configurations
        bool_opt = [o for o in self.optlist if '=' not in o]
        enum_opt = [o for o in self.optlist if '=' in o]
        total_opts = len(bool_opt)
        configs = []

        if n <= 0:
            n = self.random_config_count

        for _ in range(n):
            selected_indices = [i for i in range(total_opts) if random.random() < 0.5]
            bool_config = [
                (
                    bool_opt[i]
                    if i in selected_indices
                    else self.get_opt_negation(bool_opt[i])
                )
                for i in range(total_opts)
            ]
            enum_config = [
                f"{o.split('=')[0]}={random.choice(o.split('=')[1].replace('[','').replace(']','').split('|'))}"
                for o in enum_opt
            ]

            configs.append("-O3 " + " ".join(bool_config) + " " + " ".join(enum_config)) # type: ignore
        return configs

    def build_and_test(self, opt_config: str) -> int:
        if self.manager is None:
            raise ValueError("Manager instance is not provided.")

        self.build_and_test_count += 1
        passed = self.manager.build(opt_config=opt_config)
        if passed != 0:
            self._format_build_result(opt_config, -1)
            return -1

        if self.run_tests:
            perf = self.manager.test(num_repeats=self.num_repeats)
            if self.print_build_result and self.O3_perf is not None:
                assert self.O3_perf > 0, "O3_perf must be greater than 0 if provided."
                self._format_build_result(opt_config, perf / self.O3_perf)
            else:
                self._format_build_result(opt_config, perf)
            perf_in_range = (self.min_perf <= perf <= self.max_perf)
            if self.invert_perf_result:
                perf_in_range = not perf_in_range
            if not perf_in_range:
                return -1
        else:
            self._format_build_result(opt_config, 0)
        return 0

    def reduceFlags(self, optList):
        level = optList[0]
        del optList[0]
        start = 0
        step = len(optList) / 2
        step = int(step)
        end = len(optList) if start + step > len(optList) else start + step
        while step >= 1:
            while start < len(optList):
                self._log(
                    "[reduceFlags] [len="
                    + str(len(optList))
                    + ", s="
                    + str(start)
                    + ", e="
                    + str(end)
                    + ", step="
                    + str(step)
                    + "]",
                    flush=True,
                )
                self._log(optList)

                tmpOpt = optList[:start] + optList[end:]
                passed = self.build_and_test(
                    opt_config=" -g " + level + " " + " ".join(tmpOpt)
                )
                if passed != 0:
                    self._log("[reduceFlags] failed")
                    optList = tmpOpt[:]
                    end = len(optList) if start + step > len(optList) else start + step
                else:
                    self._log("[reduceFlags] pass")
                    start = end
                    end = len(optList) if start + step > len(optList) else start + step
            start = 0
            step = step / 2
            step = int(step)
            end = len(optList) if start + step > len(optList) else start + step
        optList.insert(0, level)
        return optList

    def reduceMore(self, optList):
        level = optList[0]
        del optList[0]
        inx = 0
        while inx < len(optList):
            self._log("[len=" + str(len(optList)) + ", inx=" + str(inx) + "]")
            self._log(optList)

            tmpOpt = optList[:inx] + optList[inx + 1 :]

            passed = self.build_and_test(
                opt_config=" -g " + level + " " + " ".join(tmpOpt)
            )
            if passed != 0:
                self._log("[reduceMore] failed")
                optList = tmpOpt[:]
            else:
                self._log("[reduceMore] pass")
                inx += 1
        optList.insert(0, level)
        return optList

    def opt_is_valid(self, opt: str) -> bool:
        if '=' in opt:
            for o in self.optlist:
                if '=' not in o:
                    continue
                if opt.split('=')[0] != o.split('=')[0]:
                    continue
                if opt.split('=')[1] not in o.split('=')[1].replace('[','').replace(']', '').split('|'):
                    continue
                return True
        else:
            return opt in self.optlist or self.get_opt_negation(opt) in self.optlist
        assert 0
        return False

    def run(self):
        pass_ratio = 0.0
        results = []
        idx = 0
        while pass_ratio < self.pass_ratio:
            opt_configs = self.random_opts()
            for config in opt_configs:
                self._log(f"[main] {idx}-th opt", flush=True)
                self._log(f"[run] Original config: {config}", flush=True)

                idx += 1
                opt_dict = {}
                for opt in config.split():
                    if opt in ["-O3", "-O2", "-O1", "-O0", "-g", "-others"]:
                        opt_dict[opt] = True
                        continue
                    assert self.opt_is_valid(opt), f"Option {opt} not recognized.{self.optlist}"
                    
                    if (
                        "=" not in opt and
                        opt not in self.optlist
                        and self.get_opt_negation(opt) in self.optlist
                    ):
                        opt_dict[self.get_opt_negation(opt)] = False
                    else:
                        opt_dict[opt] = True

                c = ConstrainsSolver(constrains_file=self.constrains_file)
                c.solve(opt_config=opt_dict)

                processed_opt = " ".join(
                    [
                        opt if opt_dict[opt] else self.get_opt_negation(opt)
                        for opt in opt_dict
                    ]
                )

                self._log(f"[run] After constraint solving: {processed_opt}", flush=True)
                passed = self.build_and_test(opt_config=" -g " + processed_opt)

                if passed == 0:
                    results.append(1)
                    results = results[-10:]
                    pass_ratio = sum(results) / len(results)
                    self._log(f"[run] Current pass ratio: {pass_ratio}", flush=True)
                    continue

                results.append(0)

                o = processed_opt.split(" ")
                o = self.reduceFlags(o)
                o = self.reduceMore(o)
                self._log(f"[run] After reduction: {' '.join(o)}", flush=True)
                result = " ".join(o)
                if len(o) > 1:
                    result = (
                        result.replace("-O3", "")
                        .replace("-O2", "")
                        .replace("-O1", "")
                        .replace("-O0", "")
                    )
                    with open(self.constrains_file, "a") as fc:
                        fc.write(result.strip() + "\n")

                if len(results) >= 10:
                    results = results[-10:]
                    pass_ratio = sum(results) / len(results)
                    self._log(f"[run] Current pass ratio: {pass_ratio}", flush=True)

    def reduce_config_until_pass(self, config: str) -> None:
        idx = 0
        start_build_and_test_count = self.build_and_test_count
        self.last_reduce_config_build_count = 0
        while True:
            self._log(f"[reduce_config_until_pass] {idx}-th opt", flush=True)
            self._log(f"[reduce_config_until_pass] Original config: {config}", flush=True)
            idx += 1

            opt_dict = {}
            for opt in config.split():
                if opt in ["-O3", "-O2", "-O1", "-O0", "-g", "-others"]:
                    opt_dict[opt] = True
                    continue
                assert self.opt_is_valid(opt), f"Option {opt} not recognized.{self.optlist}"

                if (
                    "=" not in opt
                    and opt not in self.optlist
                    and self.get_opt_negation(opt) in self.optlist
                ):
                    opt_dict[self.get_opt_negation(opt)] = False
                else:
                    opt_dict[opt] = True

            c = ConstrainsSolver(constrains_file=self.constrains_file)
            c.solve(opt_config=opt_dict)

            processed_opt = " ".join(
                [
                    opt if opt_dict[opt] else self.get_opt_negation(opt)
                    for opt in opt_dict
                ]
            )

            self._log(
                f"[reduce_config_until_pass] After constraint solving: {processed_opt}",
                flush=True,
            )
            passed = self.build_and_test(opt_config=" -g " + processed_opt)
            if passed == 0:
                self.last_reduce_config_build_count = (
                    self.build_and_test_count - start_build_and_test_count
                )
                self._log("[reduce_config_until_pass] pass", flush=True)
                self._log(
                    "[reduce_config_until_pass] total build_and_test count: "
                    f"{self.last_reduce_config_build_count}",
                    flush=True,
                )
                return

            opts = processed_opt.split(" ")
            opts = self.reduceFlags(opts)
            opts = self.reduceMore(opts)
            self._log(
                f"[reduce_config_until_pass] After reduction: {' '.join(opts)}",
                flush=True,
            )

            if len(opts) > 1:
                result = (
                    " ".join(opts)
                    .replace("-O3", "")
                    .replace("-O2", "")
                    .replace("-O1", "")
                    .replace("-O0", "")
                )
                with open(self.constrains_file, "a") as fc:
                    fc.write(result.strip() + "\n")


"""
/home/whq/autotunning/prepare
python reduce_constrains/reduce_constrain.py
"""

"""
python reduce_constrains/reduce_constrain.py \
    --manager=redis \
    --constrains_file="/home/whq/workspace/autotuning/search/src/config/redis.all01.constrains.txt" \
    --pass_ratio=0.95   \
    --random_config_count=15 \
    --min_perf=0 \
    --max_perf=15000000 \
    --opt_file=/home/whq/workspace/autotuning/search/src/config/optimization.txt \
    --run_tests
"""

# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="OptReducer Runner")
#     parser.add_argument("--manager", type=str, default="fast", help="Manager type")
#     parser.add_argument("--opt_file", type=str, default="opt_list.txt", help="Path to opt list file")
#     parser.add_argument("--constrains_file", type=str, default="reduce_constrains/constrains/fast.txt", help="Path to constrains file")
#     parser.add_argument("--random_config_count", type=int, default=20, help="Number of random configs per round")
#     parser.add_argument("--min_perf", type=float, default=1e-3, help="Minimum performance threshold")
#     parser.add_argument("--max_perf", type=float, default=1e6, help="Maximum performance threshold")
#     parser.add_argument("--pass_ratio", type=float, default=0.95, help="Pass ratio threshold")
#     parser.add_argument("--run_tests", action="store_true", help="Whether to run tests after building")
#     args = parser.parse_args()

#     manager = ManagerFactory.get_manager(args.manager)
#     reducer = OptReducer(
#         manager=manager,
#         opt_file=args.opt_file,
#         constrains_file=args.constrains_file,
#         random_config_count=args.random_config_count,
#         min_perf=args.min_perf,
#         max_perf=args.max_perf,
#         pass_ratio=args.pass_ratio,
#         run_tests=args.run_tests,
#     )
#     reducer.run()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OptReducer Runner")
    parser.add_argument("--manager", type=str, default="fast", help="Manager type")
    parser.add_argument("--opt_file", type=str, default="opt_list.txt", help="Path to opt list file")
    parser.add_argument("--constrains_file", type=str, default="reduce_constrains/constrains/fast.txt", help="Path to constrains file")
    parser.add_argument("--random_config_count", type=int, default=20, help="Number of random configs per round")
    parser.add_argument("--min_perf", type=float, default=1e-3, help="Minimum performance threshold")
    parser.add_argument("--max_perf", type=float, default=1e6, help="Maximum performance threshold")
    parser.add_argument("--pass_ratio", type=float, default=0.95, help="Pass ratio threshold")
    parser.add_argument("--run_tests", action="store_true", help="Whether to run tests after building")
    args = parser.parse_args()

    from managers.manager_factory import ManagerFactory

    manager = ManagerFactory.get_manager(args.manager, )
    reducer = OptReducer(
        manager=manager,
        opt_file=args.opt_file,
        constrains_file=args.constrains_file,
        random_config_count=args.random_config_count,
        min_perf=args.min_perf,
        max_perf=args.max_perf,
        pass_ratio=args.pass_ratio,
        run_tests=args.run_tests,
    )
    reducer.run()
