#!/usr/bin/env python3
import os
import queue
import socket
import shutil
import subprocess
from pathlib import Path
from utils.line_level_analysis import LineAnalysis
import threading
import time

class MySQLManager:
    """MySQL 源码构建工具类"""
    
    def __init__(self, build_home: str = None,
                 install_home: str = None,
                 data_home: str = None,
                 test_home: str = None,
                 cnf_file: str = "/etc/my.cnf") :
        self.build_home = Path(build_home)
        self.install_home = Path(install_home)
        self.data_home = Path(data_home)
        self.test_home = Path(test_home)
        self.cnf_file = cnf_file

        # 子目录配置
        self.data_subdirs = ["data", "run", "tmp", "log"]
        
    def clean_directory(self, directory: Path) -> None:
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    def clean(self) -> None:
        """准备构建目录结构"""
        subprocess.run(["pkill", "-9", "mysqld"], stderr=subprocess.DEVNULL)
        self.clean_directory(self.data_home)
        for subdir in self.data_subdirs:
            (self.data_home / subdir).mkdir(parents=True, exist_ok=True)
        try:
            # 清理主目录
            self.clean_directory(self.build_home)
            self.clean_directory(self.install_home)

            self.build_home.mkdir(parents=True, exist_ok=True)
            self.install_home.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError(f"Directory preparation failed: {e}")

    def clean_cache(self):
        self.clean_directory(self.data_home / "data")
        (self.data_home / "data").mkdir(parents=True, exist_ok=True)
        for subdir in self.data_subdirs:
            (self.data_home / subdir).mkdir(parents=True, exist_ok=True)
    
    def run_cmake(self, opt_config:str="-O3") -> int:
        """执行CMake配置"""
        cmake_cmd = [
            "cmake", "..",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={self.install_home}",
            f"-DMYSQL_DATADIR={self.data_home}/data",
            f"-DWITH_BOOST={self.build_home.parent / 'boost/boost_1_73_0'}",
            "-DOPENSSL_ROOT_DIR=/usr/include/openssl",
            f"-DCMAKE_C_FLAGS_RELEASE='-g -DNDEBUG {opt_config}'",
            f"-DCMAKE_CXX_FLAGS_RELEASE='-g -DNDEBUG {opt_config}'"
        ]
        
        p = subprocess.run(cmake_cmd,
                        #    stdout=subprocess.DEVNULL,
                        #    stderr=subprocess.PIPE,
                           cwd=self.build_home)
        if p.returncode != 0:
            return -1
        return 0
    
    def compile_and_install(self) -> int:
        """执行编译和安装"""
        compile_cmds = [
            ["make", "-j64"],
            ["make", "install"]
        ]
        
        for cmd in compile_cmds:
            p = subprocess.run(cmd,
                        #    stdout=subprocess.DEVNULL,
                        #    stderr=subprocess.PIPE,
                           cwd=self.build_home)
            if p.returncode != 0:
                return -1
        return 0

    def set_permissions(self) -> None:
        """设置文件权限"""
        try:
            mysql_server = self.install_home / "support-files" / "mysql.server"
            if mysql_server.exists():
                mysql_server.chmod(0o777)
            
            data_dir = self.data_home / "data"
            if data_dir.exists():
                data_dir.chmod(0o755)
        except Exception as e:
            raise RuntimeError(f"Permission setting failed: {e}")

    def build(self, opt_config:str) -> int:
        """执行完整构建流程"""
        self.clean()
        
        if self.run_cmake(opt_config=opt_config) != 0:
            print("CMake configuration failed", flush=True)
            return -1
        
        if self.compile_and_install() != 0 :
            print("Compilation failed")
            return -1
        
        self.set_permissions()
        
        return 0

    def prepare_test(self):
        """Initialize and start MySQL server"""
        self.clean_cache()
        
        subprocess.run(
            ["./mysqld", f"--defaults-file={self.cnf_file}", "--initialize"],
            cwd=self.install_home / "bin",
            
            check=True,
        )
        
        try:
            subprocess.run(
                f"""CPUPROFILE_FREQUENCY=1000 CPUPROFILESIGNAL=12 LD_PRELOAD="/usr/local/lib/libprofiler.so.0" CPUPROFILE={str(self.install_home / "bin/main.prof")} ./mysqld --defaults-file={self.cnf_file} --datadir={self.data_home}/data --socket={self.data_home}/run/mysql.sock --skip-grant-tables""",
                # shell=True,
                cwd=self.install_home / "bin",
                check=True,
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                timeout=2 * 60 * 60  # 设置超时时间为1小时
            )
        except Exception as e:
            print(f"Error {e}")
            return -1
        
        return 0

    def run_benchmark(self, result_queue):
        mysql_bin = self.install_home / "bin"
        mysql_cmd = str(mysql_bin / "mysql")
        mysql_sock = str(self.data_home / "run/mysql.sock")
        mysqladmin_cmd = str(mysql_bin / "mysqladmin")
        
        if not self.wait_mysql_start():
            return float('inf')
        
        try:
            # 阶段1：初始化测试数据库
            subprocess.run(
                [
                    mysql_cmd,
                    "-u", "root",
                    f"--socket={mysql_sock}",
                    "-e", "DROP DATABASE IF EXISTS test; CREATE DATABASE test;"
                ],
                cwd=self.install_home / "bin",
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # 阶段2：执行性能测试
            start_time = time.time()
            subprocess.run(
                f"""
                killall -12 {self.install_home / 'bin/mysqld'};
                bash _run_all_test.sh -d {self.data_home};
                killall -12 {self.install_home / 'bin/mysqld'};
                """,
                
                cwd=self.test_home,
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            subprocess.run(
                    [
                        mysqladmin_cmd,
                        "-u", "root",
                        f"--socket={mysql_sock}",
                        "shutdown"
                    ],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    timeout=35,
                    check=False  # 允许关闭失败
                )
            duration = time.time() - start_time
            
            # 阶段3：生成分析报告
            if (self.install_home / "bin/main.prof").exists():
                subprocess.run(
                    f"pprof --lines mysqld main.prof >> {str(self.install_home / 'bin/report.txt')}"
                    ,
                    shell=True,  # 需要shell支持重定向
                    cwd=self.install_home / "bin",
                    check=False  # 允许报告生成失败
                )
                (self.install_home / "bin/main.prof").unlink()

            # 提交结果
            result_queue.put(duration)
            return duration

        except subprocess.CalledProcessError as e:
            error_msg = f"Benchmark failed at phase:\n{e.cmd}\nExit code: {e.returncode}\n{e.stderr.decode()}"
            print(error_msg)
            result_queue.put(-1.0)

        finally:
            # 阶段4：清理（独立异常处理）
            try:
                subprocess.run(
                    [
                        mysqladmin_cmd,
                        "-u", "root",
                        f"--socket={mysql_sock}",
                        "shutdown"
                    ],
                    timeout=10,
                    check=False  # 允许关闭失败
                )
            except Exception as e:
                print(f"Cleanup warning: {str(e)}")
    
    def wait_mysql_start(self, timeout: int = 300) -> bool:
        def check_by_socket():
            return (self.data_home / "run/mysql.sock").exists()

        def check_by_mysqladmin():
            try:
                cmd = [
                    "./mysqladmin",
                    "ping",
                    f"--socket={self.data_home}/run/mysql.sock"
                ]
                result = subprocess.run(
                    cmd,
                    cwd=self.install_home / "bin",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5
                )
                return "mysqld is alive" in result.stdout.decode()
            except:
                return False

        # 三重检测机制
        start_time = time.time()
        while time.time() - start_time < timeout:
            if check_by_socket() and check_by_mysqladmin():
                time.sleep(2)
                return True
            time.sleep(1)
        return False
    
    def test(self):
        """主测试方法"""
        time.sleep(5)
        subprocess.run(["pkill", "-9", "mysqld"], stderr=subprocess.DEVNULL)
        
        result_queue = queue.Queue()
        # 启动线程
        mysql_thread = threading.Thread(target=self.prepare_test)
        bench_thread = threading.Thread(target=self.run_benchmark, args=(result_queue,))
        
        mysql_thread.start()
        bench_thread.start()

        # 等待测试完成
        mysql_thread.join()
        bench_thread.join(timeout=1 * 60 * 60)
        
        if not result_queue.empty():
            return float(result_queue.get())
        return -1

    @staticmethod
    def analysis_report(management1: 'MySQLManager', management2: 'MySQLManager'):
        func_file1 = management1.build_home.parent / "project-func-info-databaase.json"
        func_file2 = management2.build_home.parent / "project-func-info-databaase.json"
        report_file1 = management1.install_home / "bin/report.txt"
        report_file2 = management2.install_home / "bin/report.txt"
        
        analysis1 = LineAnalysis(func_file=func_file1, report_file=report_file1)
        analysis2 = LineAnalysis(func_file=func_file2, report_file=report_file2)
        result1 = analysis1.parse_report()
        result2 = analysis2.parse_report()
        if not result1 or not result2:
            return []
        for key in result1:
            modified_key = (key[0].replace("mysql/mysql", "mysql/mysqlO3"),) + key[1:]
            result1[key].extend(result2.get(modified_key, [0, 0]))
        result_list = []

        for key, value in result1.items():
            result_list.append(list(key) + value)
        return result_list
    
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="MySQL Build System")
    parser.add_argument("--optimize", type=str, default="",
                       help="Compiler optimization flags (e.g. '-O3 -march=native')")
    
    args = parser.parse_args()
    build_home = "/home/whq/dataset/mysql/mysqlO3/build"
    install_home = "/home/whq/bin/mysqlO3"
    data_home = "/home/whq/data/mysqlO3"
    test_home = "/home/whq/dataset/mysql/sysbench"
    cnf_file = "/home/whq/dataset/mysql/mysqlO3.cnf"
    
    try:
        builder = MySQLManager(build_home=build_home,
                               install_home=install_home,
                               data_home=data_home,
                               test_home=test_home,
                               cnf_file=cnf_file)
        # builder.build("-g -O3")
        builder.test()
    except KeyboardInterrupt:
        print("\nBuild interrupted by user")
        exit(1)
    except Exception as e:
        exit(1)