import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

from utils.line_level_analysis import LineAnalysis

class RocksDBManager():
    def __init__(self, build_home:str, install_home:str, db_home:str):
        self.build_home = Path(build_home)      # /home/whq/dataset/rocksdb/rocksdb/build
        self.install_home = Path(install_home)  # /home/whq/bin/rocksdb
        self.db_home = Path(db_home)
        pass
    
    @property
    def os_env(self)->dict:
        env = os.environ.copy()
        env["CPLUS_INCLUDE_PATH"] = os.path.join(self.install_home, "include") + ":" + env.get("CPLUS_INCLUDE_PATH", "")
        env["LD_LIBRARY_PATH"] = os.path.join(self.install_home, "lib64") + ":" + env.get("LD_LIBRARY_PATH", "")
        env["LIBRARY_PATH"] = os.path.join(self.install_home, "lib64") + ":" + env.get("LIBRARY_PATH", "")
        env["PATH"] = os.path.join(self.install_home, "tools") + ":" + env.get("PATH", "")
        return env
    
    def build(self, opt_config:str = " -g -O3 ", jobs=32) -> int:
        # return 0
        self.clean()  # 清理旧数据
        try:
            subprocess.run([
                "cmake",
                "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
                f"-DCMAKE_C_FLAGS_RELWITHDEBINFO={shlex.quote( '-g ' + str(opt_config) + ' -DNDEBUG')}",  # 处理特殊字符
                f"-DCMAKE_CXX_FLAGS_RELWITHDEBINFO={shlex.quote( '-g ' + str(opt_config) + ' -DNDEBUG')}",
                f"-DCMAKE_INSTALL_PREFIX={shlex.quote(str(self.install_home))}",
                "-DWITH_SNAPPY=ON",
                "-DWITH_ZLIB=ON",
                "-DWITH_LZ4=ON",
                "-DUSE_RTTI=ON",
                "-DWITH_ZSTD=ON",
                "-DWITH_BZIP2=ON",
                ".."
            ],
            cwd=str(self.build_home),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
            )

            p = subprocess.run(["make", f"-j{jobs}"], cwd=self.build_home, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            # p = subprocess.run(["make", f"-j{jobs}"], cwd=self.build_home,  check=True)
            if p.returncode != 0:
                print("Make failed!")
                return -1
            subprocess.run(["make", "install"], cwd=self.build_home, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            # subprocess.run(["make", "install"], cwd=self.build_home, check=True)
            
            # 复制额外文件
            (self.install_home/"tools").mkdir(exist_ok=True)
            subprocess.run([
                "cp", "-p",
                str(self.build_home.parent/"utilities/merge_operators.h"),
                str(self.install_home/"include/rocksdb/utilities/")
            ], check=True)
            subprocess.run(["cp", "-p", str(self.build_home/"tools/ldb"), str(self.install_home/"tools")], check=True)
            subprocess.run(["cp", "-p", str(self.build_home/"tools/sst_dump"), str(self.install_home/"tools")], check=True)
            return 0
        except subprocess.CalledProcessError:
            return -1
        
        
    def test(self, num_repeat: int = 1) -> float:
        def run_for_once() -> float:
            # 1. 确保目录可写（尤其对子进程）
            self.db_home.mkdir(exist_ok=True)  # 使用八进制表示 0o777

            # 2. 运行命令（确保 db_bench 正常退出）
            commands=f"""
                time LD_PRELOAD=/usr/local/lib/libprofiler.so.0 CPUPROFILE=./main.prof ./db_bench --benchmarks=readwhilewriting --db={self.db_home} --reads=30000000 --writes=30000000
                pprof --lines ./db_bench main.prof >> report.txt
            """

            try:
                p = subprocess.run(
                    commands,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=True,
                    env=self.os_env,
                    cwd=self.build_home,
                    check=True  # 如果命令失败则抛出异常
                )
            except subprocess.CalledProcessError as e:
                
                print(f"Command failed with error: {e}")
                return float('inf')  # 返回无穷大表示失败
            # 3. 确保 db_bench 已退出后再删除目录
            time.sleep(1)  # 短暂延迟确保资源释放
            shutil.rmtree(str(self.db_home))  # 强制转为字符串路径

            # 4. 解析时间结果
            stdouts = p.stderr.decode("utf-8").split("\n")
            for out in stdouts:
                if out.startswith("real"):
                    out = out.replace("real\t", "")
                    nums = re.findall(r"\d*\.?\d+", out)
                    assert len(nums) == 2, "Expect %dm %ds format"
                    secs = float(nums[0]) * 60 + float(nums[1])
                    print(f"The execute time = {secs:.2f}s")
                    return secs
            raise RuntimeError("Failed to parse time output")

        # 多次运行取平均值
        total_secs = 0.0
        for _ in range(num_repeat):
            total_secs += run_for_once()
            
        src = self.build_home / "report.txt"
        dest = self.build_home.parent / "report.txt"

        shutil.move(str(src), str(dest))
        
        return total_secs / num_repeat
    
    def clean(self):
        clean_dirs = [self.build_home, self.install_home]
        for clean_dir in clean_dirs:
            if clean_dir.exists():
                shutil.rmtree(clean_dir)
                clean_dir.mkdir(parents=True, mode=0o755)
        if (self.build_home.parent / "report.txt").exists():
            os.unlink(self.build_home.parent / "report.txt")

    @staticmethod
    def analysis_report(manager1:'RocksDBManager', manager2:'RocksDBManager'):
        func_file1 = manager1.build_home.parent / "project-func-info-databaase.json"
        func_file2 = manager2.build_home.parent / "project-func-info-databaase.json"
        report_file1 = manager1.build_home.parent / "report.txt"
        report_file2 = manager2.build_home.parent / "report.txt"
        analysis1 = LineAnalysis(func_file=func_file1, report_file=report_file1)
        analysis2 = LineAnalysis(func_file=func_file2, report_file=report_file2)
        result1 = analysis1.parse_report()
        result2 = analysis2.parse_report()
        if not result1 or not result2:
            return []
        for key in result1:
            # print(f"key = {key}")
            modified_key = (key[0].replace("rocksdb/rocksdb", "rocksdb/rocksdbO3"),) + key[1:]
            result1[key].extend(result2.get(modified_key, [0, 0]))
        result_list = []

        for key, value in result1.items():
            result_list.append(list(key) + value)
        return result_list
        

if __name__ == "__main__":
    build_home = "/home/whq/dataset/rocksdb/rocksdb/build"
    install_home = "/home/whq/bin/rocksdb"
    db_home = "/home/whq/bin/db_rocksdb"
    
    build_home_base = "/home/whq/dataset/rocksdb/rocksdbO3/build"
    install_home_base = "/home/whq/bin/rocksdbO3"
    db_home_base = "/home/whq/bin/db_rocksdbO3"
    # db_home = "/home/whq/bin/db_rocksdb"
    
    manager = RocksDBManager(build_home=build_home, install_home=install_home, db_home=db_home)
    manager_base = RocksDBManager(build_home=build_home_base, install_home=install_home_base, db_home=db_home_base)

    opt_config1 = "-g -O2 "
    opt_config2 = "-g -Ofast "
    
    if manager.build(opt_config1) == 0:
        print("Build successful!")
    
    if manager_base.build(opt_config2) == 0:
        print("Base build successful!")
    
    print(manager.test(num_repeat=1))
    print(manager_base.test(num_repeat=1))
    report_infos = RocksDBManager.analysis_report(manager, manager_base)
    for report_info in report_infos:
        print(report_info)
    

