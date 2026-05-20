import os
import re
import subprocess
from pathlib import Path
import time

from utils.line_level_analysis import LineAnalysis
FLOAT_MAX = float('inf')

class cBenchManager:
    def __init__(self, path, artifact='a.out', cpucore=319):
        self.path = Path(path)
        self.artifact = artifact
        self.benchmarks = []
        self.cpucore = cpucore
        
    def build(self, str_opt_setting):
        commands = f"""cd {self.path};
        taskset -c {self.cpucore} make clean > /dev/null 2>/dev/null;
        taskset -c {self.cpucore} make -j2 CCC_OPTS_ADD="{str_opt_setting}" LD_OPTS=" -o {self.artifact} -fopenmp" > /dev/null 2>/dev/null;
        """
        subprocess.Popen(commands, stdout=subprocess.PIPE, shell=True).wait()

        # Check if build fails
        if not os.path.exists(self.path / self.artifact):
            print(f"Build failed for {self.path}")
            return -1
        return 0
    

    def test(self, input_id=1):
        run_commands = f"""cd {self.path};
        taskset -c {self.cpucore} ./_ccc_check_output.clean ;
        taskset -c {self.cpucore} ./__run {input_id} 2>&1;
        """

        tot = 0

        # Run the executable
        p = subprocess.Popen(run_commands, stdout=subprocess.PIPE, shell=True)
        p.wait()
        stdouts = p.stdout.read().decode('ascii').split("\n")

        for out in stdouts:
            if out.startswith("real"):
                out = out.replace("real\t", "")
                nums = re.findall("\d*\.?\d+", out)
                assert len(nums) == 2, "Expect %dm %ds format"
                secs = float(nums[0])*60+float(nums[1])
                tot += secs
        return tot

    def clean(self):
        commands = f"""cd {self.path};
        taskset -c {self.cpucore} make clean > /dev/null 2>/dev/null;
        taskset -c {self.cpucore} ./_ccc_check_output.clean ;
        """
        subprocess.Popen(commands, stdout=subprocess.PIPE, shell=True).wait()
    
    @staticmethod
    def analysis_report(management1: "cBenchManager", management2: "cBenchManager"):
        func_file1 = os.path.join(management1.path, "project-func-info-databaase.json")
        func_file2 = os.path.join(management2.path, "project-func-info-databaase.json")
        report_file1 = os.path.join(management1.path, "report.txt")
        report_file2 = os.path.join(management2.path, "report.txt")
        
        analysis1 = LineAnalysis(func_file=func_file1, report_file=report_file1)  # type: ignore
        analysis2 = LineAnalysis(func_file=func_file2, report_file=report_file2)  # type: ignore
        result1 = analysis1.parse_report()
        result2 = analysis2.parse_report()
        if not result1 or not result2:
            return []
        for key in result1:
            modified_key = (key[0].replace(str(management1.path), str(management2.path)),) + tuple(key[1:])
            result1[key].extend(result2.get(modified_key, [0, 0]))
        result_list = []

        for key, value in result1.items():
            result_list.append(list(key) + value)
        return result_list
        
if __name__ == "__main__":
    benchmark_home = "/home/whq/dataset/cBench/cbench-instance0/"
    # benchmark_list = ['network_dijkstra']
    benchmark_list = [
        'network_dijkstra', 'security_rijndael_e', 'office_rsynth', 'telecom_adpcm_c', 'telecom_adpcm_d',
        'bzip2e', 'automotive_susan_c', 'telecom_gsm', 'automotive_bitcount', 'consumer_tiffdither',
        'consumer_jpeg_c', 'security_blowfish_d', 'consumer_tiff2rgba', 'automotive_qsort1',
        'consumer_tiffmedian', 'bzip2d', 'consumer_jpeg_d', 'network_patricia', 'security_sha',
        'office_stringsearch1', 'automotive_susan_e', 'telecom_CRC32', 'automotive_susan_s', 'consumer_tiff2bw'
    ]
    for benchmark in benchmark_list:
        manager1 = cBenchManager(os.path.join(benchmark_home, benchmark, "src"), "a.out", cpucore=319)
        begin_time = time.time()
        ret = manager1.build("-g -O3")
        if ret == -1:
            continue
        print(f"Build time for {benchmark} with -O3: {time.time() - begin_time:.2f} seconds", flush=True)
        tier_heavy = [
            'security_rijndael_e'
        ]
        
        tier_medium_heavy = [
            "security_blowfish_d", "security_sha", "consumer_tiff2rgba",
            "consumer_jpeg_d"
        ]

        tier_medium = [
            'consumer_tiffdither', 'consumer_jpeg_c', 'consumer_tiff2bw', 'bzip2d']
        
        tier_light = [
            'consumer_tiffmedian', 'bzip2e', 'telecom_adpcm_d', 'automotive_qsort1',
            'telecom_gsm', 'automotive_susan_c', 'automotive_susan_e', 'telecom_CRC32', 'telecom_adpcm_c',
            'automotive_susan_s', 'automotive_bitcount', 'office_stringsearch1',
            'office_rsynth', 'network_patricia', 'network_dijkstra'
        ]
        print(f"len of all tiers: {len(tier_heavy)+len(tier_medium_heavy)+len(tier_medium)+len(tier_light)}", flush=True)
        
        if benchmark in tier_heavy:
            num_repeats = 10
        elif benchmark in tier_medium_heavy:
            num_repeats = 15
        elif benchmark in tier_medium:
            num_repeats = 24
        else:
            num_repeats = 30
        for i in range(num_repeats):
            res = manager1.test()
        print(f"Test time for {benchmark} with -O3: {time.time() - begin_time:.2f} seconds", flush=True)

        manager1.clean()
    # manager1 = cBenchManager(os.path.join(benchmark_home, "network_dijkstra", "src"), "a.out")
    # manager2 = cBenchManager(os.path.join(benchmark_home+"O3", "network_dijkstra", "src"), "a.out")
    # manager1.build("-g -O3")
    # manager2.build("-g -O3")
    # for i in range(1):
    #     manager2.test()
    #     manager1.test()
    
        
    # report_infos = cBenchManager.analysis_report(manager1, manager2)
    # print(report_infos)
    # manager1.clean()
    # manager2.clean()