from concurrent.futures import ThreadPoolExecutor
import csv
import os
import shlex
import subprocess
from typing import Union

class FAISSManager:
    def __init__(self, build_dir: str, env: str, ann_dir: str, repeat: int = 1, silence:bool=True, datasets: list[str] = None, enable_gperftools: bool = False):
        self.build_dir = build_dir
        self.env = env
        self.ann_dir = ann_dir
        self.repeat = repeat
        self.datasets = datasets
        self.enable_gperftools = enable_gperftools
        self.silence = silence

    def run_py_command_in_env(self, command_list:Union[list[str], str], env:Union[str, None]=None, cwd:Union[str, None]=None, timeout:Union[int, None]=1800) -> int:
        """
        Run a command in a specific conda environment.
        :param command_list: The command to run, either as a string or a list of strings.
        :param env: The name of the conda environment to use.
        :param cwd: The working directory to run the command in.
        """
        
        if self.silence:
            stdout, stderr = subprocess.DEVNULL, subprocess.DEVNULL
        else:
            stdout, stderr = None, None

        cwd = cwd or self.build_dir
        env = env or self.env
        command = " && ".join(command_list) if isinstance(command_list, list) else command_list
        finally_command = f"conda run -n {env} bash -c {shlex.quote(command)}"

        try:
            p = subprocess.run(
                finally_command, stderr=stderr, stdout=stdout, shell=True, cwd=cwd, timeout=timeout
            )
            
        except subprocess.TimeoutExpired as e:
            print(f"Command timed out: {command}")
            return -1
        
        return p.returncode
    

    def build(self, opt_config: str = "-g -O3", jobs: int = 128) -> Union[int, None]:
        os.sched_setaffinity(0, set(range(0, 128)))
        if self.clean() != 0:
            print("Clean failed, please check the environment.")
            return -1

        PYTHON_EXE = f"/home/whq/bin/anaconda3/envs/{self.env}/bin/python"
        cmake_command = f"""
            cmake -B build . \
            -DFAISS_ENABLE_GPU=OFF \
            -DFAISS_ENABLE_PYTHON=ON \
            -DBUILD_TESTING=OFF \
            -DBUILD_SHARED_LIBS=ON \
            -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_INSTALL_PREFIX={self.build_dir}/install \
            -DPython_EXECUTABLE={PYTHON_EXE} \
            -DCMAKE_CXX_FLAGS_RELEASE="-g {opt_config}" \
        """
        
        build_command = f"make -C build -j {jobs}"
        install_command = f"make -C build install"
        build_python_command = f"make -C build -j {jobs} swigfaiss"
        check_command = "python -c 'import faiss'"
        
        command_list = [
            cmake_command,
            build_command,
            install_command,
            build_python_command,
            check_command,
        ]
        for command in command_list:
            if self.run_py_command_in_env(command, cwd=self.build_dir) != 0:
                print(f"Command failed: {command}")
                return -1
        
        # Command are executed seperately because some require a specific working directory
        faiss_python_dir = os.path.join(self.build_dir, "build", "faiss", "python")
        reutrn_code = self.run_py_command_in_env("python setup.py install", cwd=faiss_python_dir)
        if reutrn_code != 0:
            print("Failed to install faiss python package.")
            return -1
        return 0
    
    
    def clean_cache(self) -> int:
        out_list = [f"ann_benchmarks/profile_{dataset}.out" for dataset in self.datasets]
        out_file = ' '.join(out_list)
        clean_command = f"rm -f report.txt {out_file}"
        p = subprocess.run(clean_command, shell=True, cwd=self.ann_dir, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        
        if p.returncode != 0:
            print("Failed to clean cache.")
            return -1
        
        return 0
    
    
    def clean(self):
        self.clean_cache()
        subprocess.run("rm -rf build", shell=True, cwd=self.build_dir, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        clean_command = " pip uninstall -y faiss"
        check_after = "pip3 list | grep faiss"
        self.run_py_command_in_env(clean_command)
        faiss_flag = self.run_py_command_in_env(check_after)
        return not faiss_flag


    def run_benchmark(self) -> float:
        mapping = {}
        for dataset in self.datasets:
            result_file = f"{dataset}_results.csv"

            run_command = f"python run.py --force --threads 80 --algorithm faiss-ivf --dataset {dataset} --local "
            get_result_command = (
                f"python data_export.py --datasets {dataset} --output {result_file}"
            )

            command_list = [run_command, get_result_command]
            for command in command_list:
                if self.run_py_command_in_env(command, cwd = self.ann_dir, timeout = 15 * 60) != 0:
                    print(f"{command} wrong", flush=True)
                    return -1
            qps = self.extract_qps_from_csv(os.path.join(self.ann_dir, result_file))
            if not qps or qps == {}:
                return -1

            mapping.update(qps)
        acc = self.caculate_qps_acc(mapping, {'euclidean_4096_200.hdf5': 683.0217846335993})  # Example baseline
        if self.enable_gperftools:
            self.parse_report()
        return acc
 
    
    def parse_report(self) -> int:
        out_file = os.path.join(self.ann_dir, "ann_benchmarks/profile_sift-128-euclidean.out")
        executable = os.path.join(self.build_dir, "install/lib64/libfaiss.so")
        
        pprof_command = f"pprof --line {executable} {out_file} &>> report.txt"
        p = subprocess.run(pprof_command, shell=True, cwd=self.ann_dir)
        
        if p.returncode != 0:
            print("Failed to parse report.")
            return -1
        return 0
            
    @staticmethod
    def extract_qps_from_csv(file_path: str) -> Union[dict[str, int], None]:
        mapping = {}
        with open(file_path, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return None

            if not all(col in reader.fieldnames for col in ["filename", "qps"]):
                return None

            for row in reader:
                knn = float(row["k-nn"])
                if knn < 0.7:
                    return None
                filename = row["filename"]
                qps = float(row["qps"])
                mapping[filename] = qps
        return mapping


    @staticmethod
    def caculate_qps_acc(
        qps_dict: dict[str, float], qps_dict_base: dict[str, float]
    ) -> float:

        acc = 0
        assert len(qps_dict) == len(qps_dict_base), "两个字典的长度不一致"
        for key in qps_dict:
            acc += qps_dict[key] / qps_dict_base[key]
        return acc / len(qps_dict)

    def test(self, num_repeats=1):
        os.sched_setaffinity(0, set(range(0, 16)))
        self.clean_cache()
        result = self.run_benchmark()
        if result is None or result == -1 :
            return -1
        return result

import time
if __name__ == "__main__":
    env = "faiss"
    build_dir = "/home/whq/dataset/faiss/faiss"
    ann_dir = "/home/whq/dataset/faiss/ann-benchmarks-flags"

    manager = FAISSManager(
        build_dir=build_dir,
        env=env,
        ann_dir=ann_dir,
        datasets=["sift-128-euclidean"],
        enable_gperftools=False,
        silence=False,
    )
    now_time = time.time()
    manager.build(opt_config="-O3 -fcombine-stack-adjustments -fcompare-elim -fno-cprop-registers -fdefer-pop -fno-dse -fforward-propagate -fguess-branch-probability -fif-conversion -fno-if-conversion2 -finline -finline-functions-called-once -fno-ipa-modref -fno-ipa-profile -fno-ipa-pure-const -fno-ipa-reference -fno-ipa-reference-addressable -fmove-loop-invariants -fmove-loop-stores -fno-reorder-blocks -fno-section-anchors -fno-shrink-wrap -fsplit-wide-types -fno-ssa-phiopt -fno-thread-jumps -fno-toplevel-reorder -ftree-bit-ccp -ftree-ccp -ftree-ch -ftree-coalesce-vars -ftree-dominator-opts -fno-tree-pta -ftree-sink -ftree-sra -fno-tree-ter -fcaller-saves -fcode-hoisting -fno-crossjumping -fno-cse-follow-jumps -fdevirtualize -fdevirtualize-speculatively -fexpensive-optimizations -fno-gcse -fno-hoist-adjacent-loads -findirect-inlining -fno-inline-functions -fno-inline-small-functions -fno-ipa-bit-cp -fno-ipa-cp -fno-ipa-icf -fno-ipa-icf-functions -fno-ipa-icf-variables -fno-ipa-ra -fno-ipa-sra -fno-isolate-erroneous-paths-dereference -flra-remat -foptimize-strlen -fno-partial-inlining -fno-peephole2 -free -fno-reorder-functions -frerun-cse-after-loop -fno-schedule-insns -fschedule-insns2 -fstrict-aliasing -fno-tree-loop-distribute-patterns -ftree-loop-vectorize -fno-tree-pre -fno-tree-slp-vectorize -ftree-switch-conversion -fno-tree-tail-merge -fno-tree-vrp -fgcse-after-reload -fno-ipa-cp-clone -floop-interchange -fno-loop-unroll-and-jam -fno-peel-loops -fno-predictive-commoning -fsplit-loops -fsplit-paths -ftree-loop-distribution -ftree-partial-pre -funroll-completely-grow-size -fno-unswitch-loops -fno-version-loops-for-strides -fallow-store-data-races -fassociative-math -fno-branch-probabilities -fcrypto-accel-aes -fno-delayed-branch -ffast-math -fno-finite-math-only -fno-float-store -ffold-simple-inlines -fno-ftz -fgcse-las -fgcse-sm -fno-graphite -fno-graphite-identity -ficp -fno-icp-speculatively -fno-if-conversion-gimple -fifcvt-allow-complicated-cmps -fno-ipa-ic -fno-ipa-prefetch -fno-ipa-pta -fno-ipa-reorder-fields -fira-loop-pressure -fisolate-erroneous-paths-attribute -fno-loop-crc -floop-nest-optimize -floop-parallelize-all -fno-modulo-sched -fmodulo-sched-allow-regmoves -fnothrow-opt -fno-prefetch-loop-arrays -fno-reciprocal-math -frename-registers -fno-reorder-blocks-and-partition -fno-reschedule-modulo-scheduled-loops -fsched-spec-load -fsched-spec-load-dangerous -fno-sched2-use-superblocks -fno-sel-sched-pipelining -fsel-sched-pipelining-outer-loops -fsel-sched-reschedule-pipelined -fselective-scheduling -fno-selective-scheduling2 -fno-simdmath -fno-single-precision-constant -fsplit-ldp-stp -fno-split-wide-types-early -ftracer -fno-tree-cselim -ftree-loop-if-convert -ftree-lrs -ftree-slp-transpose-vectorize -ftree-vectorize -funroll-all-loops -fno-unroll-loops -funsafe-math-optimizations -fvariable-expansion-in-unroller -fno-vpt -fno-web -mcmlt-arith -mlow-precision-div -mlow-precision-recip-sqrt -mlow-precision-sqrt -msimdmath-64 -falign-functions=32 -falign-jumps=8 -fira-algorithm=priority -fira-region=mixed -fsched-stalled-insns-dep=8 -fstack-reuse=none -ftree-parallelize-loops=128")
    print("Build time:", time.time() - now_time)
    now_time = time.time()
    print(manager.test())
    print("Test time:", time.time() - now_time)