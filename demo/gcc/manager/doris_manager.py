# Author: whq
# Description: 使用时需注意一下几点：
# 0. 在编译脚本时需要source /home/whq/dataset/doris/doris.env
# 1. 必须使用bash build.sh --be --fe 成功编译之后才可使用这个脚本，该脚本无异常处理能力
# 2. 在load_data时, 脚本默认使用的是10G的数据, 但是tpch默认的并不支持10G, 需要自己修改tpch脚本(如果是使用我的doris文件，那已经修改过了)
# 3. 端口号并没有做修改，默认9030和9050, 也没有加修改逻辑
# 4. 最好不要从头下载doris的压缩包进行编译，非常耗时，并且有bug。可以cp -r /home/whq/dataset/doris

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import time
import psutil
import pymysql
import requests


def rm_dir(dir_path:Path):
    """删除文件夹下的所有文件，保留父目录"""
    if dir_path.exists():
        shutil.rmtree(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)

def get_folder_size(folder_path):
    total_size = 0
    for entry in os.scandir(folder_path):
        if entry.is_file():
            total_size += entry.stat().st_size  # file size, byte
        elif entry.is_dir():
            total_size += get_folder_size(entry.path)
    return total_size

class DorisManager:
    def __init__(self,
                 build_dir:str,
                 fe_ip:str = "173.52.1.2",
                 be_ip:str = "173.52.1.2",
                 fe_port:int = 9030,
                 be_port:int = 9050,
                 user:str = "root",
                 repeat:int = 20,
                 scale_factor:str = "10"
                 ):
        """
        这里能更改的只有build_dir.... 因为没有设置更改端口的方法
        程序的编译至多两个程序一起编译，而测试的时间时低于编译时间的，我认为没有必要再并行了
        """
        self.build_dir = Path(build_dir)
        self.fe_ip = fe_ip
        self.be_ip = be_ip
        self.fe_port = fe_port
        self.be_port = be_port
        self.user = user
        self.repeat = repeat
        self.scale_factor = scale_factor
        self.fe_meta_dir = self.build_dir / "data/fe/meta"
        self.be_data_dir = self.build_dir / "data/be/storage"
        self.tpch_home = self.build_dir / "tools/tpch-tools/bin"
    
    def replace_bad_config(self, opt_config:str) -> str:
        # 当-ftoplevel-reorder关闭时  -fsection-anchors也必须关闭
        if "-fno-toplevel-reorder" in opt_config:
            opt_config = opt_config.replace("-fsection-anchors", "-fno-section-anchors")
            
        # doris 需要禁用 -finline-atomics 否则会在最后一步链接时出问题
        opt_config = opt_config.replace("-finline-atomics", "")
        return opt_config
    
    def build(self, opt_config:str = " -g -O3 ") -> int:
        opt_config = self.replace_bad_config(opt_config=opt_config)
        
        self.stop_services()
        self.clean()
        
        env = os.environ.copy()
        env["DISABLE_JAVA_CHECK_STYLE"] = "ON"
        env["EXTRA_CXX_FLAGS"] = ' -g ' + opt_config
        env["DORIS_TOOLCHAIN"] = "gcc"
        jobs = min(int(0.8 * os.cpu_count()), 80)
        
        # 三个分别对应无输出编译be/fe和有输出编译be/fe以及有输出编译be
        process = subprocess.run(f"bash build.sh --be --fe -j{jobs}", cwd=self.build_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True, env=env, text=True)
        # process = subprocess.run(f"bash build.sh --be --fe -j{jobs}", cwd=self.build_dir, shell=True, env=env, text=True)
        # process = subprocess.run(f"bash build.sh --be -j{jobs}", cwd=self.build_dir, shell=True, env=env, text=True)
        
        if process.returncode != 0:
            print("Build failed")
            return -1

        for dir in [self.fe_meta_dir, self.be_data_dir]:
            dir.mkdir(parents=True, exist_ok=True)
            
        self.modify_config()
        return 0
    
    def modify_config(self) -> int:
        """修改 Doris FE 和 BE 的配置文件"""
        
        fe_conf_path = self.build_dir / "output/fe/conf/fe.conf"
        be_conf_path = self.build_dir / "output/be/conf/be.conf"

        fe_config_lines = [
            "# Network and metadata settings",
            f"priority_networks = {self.fe_ip}/32",
            f"meta_dir = {self.fe_meta_dir}"
        ]

        be_config_lines = [
            "# Network and storage settings",
            f"priority_networks = {self.be_ip}/32",
            f"storage_root_path = {self.be_data_dir}"
        ]

        with open(fe_conf_path, 'a') as f:
            f.write("\n" + "\n".join(fe_config_lines) + "\n")
        
        with open(be_conf_path, 'a') as f:
            f.write("\n" + "\n".join(be_config_lines) + "\n")

        return 0
    
    def start_doris(self):
        """启动 Doris FE 和 BE"""
        self.stop_services()
        
        be_start = os.path.join(self.build_dir, "output/be/bin/start_be.sh")
        fe_start = os.path.join(self.build_dir, "output/fe/bin/start_fe.sh")
        
        start_fe = subprocess.run(["bash", fe_start, "--daemon"], cwd=self.build_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        
        # 等待FE启动成功再启动BE
        if start_fe.returncode != 0 or self.check_fe_ready() == -1:
            return -1
        
        start_be = subprocess.run(["bash", be_start, "--daemon"], cwd=self.build_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        
        if start_be.returncode != 0:
            return -1
        return 0
    
    def register_be_to_fe(self):
        """注册 Doris BE到FE
        mysql -h 173.52.1.2 -P 9030 -u root -p
        ALTER SYSTEM ADD BACKEND '173.52.1.2:9050';"""
        try:
            conn = pymysql.connect(
                host="127.0.0.1",
                port=9030,
                user="root",
                password="",
                connect_timeout=30
            )
            cursor = conn.cursor()
            
            check_sql = f"SHOW BACKENDS;"
            cursor.execute(check_sql)
            backends = cursor.fetchall()
            
            # 检查当前 BE 是否已在列表中
            be_address = self.be_ip
            already_register_be_to_feed = any(be_address in str(backend) for backend in backends)
            if already_register_be_to_feed:
                return 0
            
            register_be_to_fe_sql = f"ALTER SYSTEM ADD BACKEND '{self.be_ip}:{self.be_port}';"
            cursor.execute(register_be_to_fe_sql)
            conn.commit()

        except pymysql.Error as e:
            print(f"操作失败: {e}")
        finally:
            if conn:
                conn.close()
    
    def check_fe_ready(self, max_attempt:int = 30, interval:int = 2) -> int:
        """检查FE是否准备就绪"""
        attempt = 0
        while attempt < max_attempt:
            try:
                resp = requests.get(f"http://{self.fe_ip}:8030/api/bootstrap", timeout=5)
                if '"msg":"success"' in resp.text:
                    return 0
            except Exception as http_err:
                time.sleep(interval)
                attempt += 1
        return -1
    
    def check_cluster_health(self):
        """
        通过MySQL协议验证服务状态
        """
        try:
            conn = pymysql.connect(
                host="127.0.0.1",
                port=9030,
                user="root",
                password="",
                database="information_schema",
                connect_timeout=30
            )
            with conn.cursor() as cursor:
                cursor.execute("SHOW FRONTENDS")
                fe_status = cursor.fetchall()

                cursor.execute("SHOW BACKENDS")
                be_status = cursor.fetchall()
                
                # 本机测试只有一个fe节点和一个be节点，但是保证代码的健壮性，还是检查所有节点
                # fe 节点存活信息在第9列，be 节点存活信息在第10列
                fe_alive = all(row[8] == "true" for row in fe_status)
                be_alive = all(row[9] == "true" for row in be_status)
                
                if fe_alive and be_alive:
                    return 0
                elif not fe_alive:
                    return -1
                else:
                    return -2
        except pymysql.Error as e:
            return -3
        finally:
            if 'conn' in locals() and conn:
                conn.close()
    
    def wait_cluster_ready(self, max_attempts:int = 30, interval:int = 2) -> int:
        """等待 Doris FE 和 BE 准备就绪"""
        time.sleep(interval * 3)
        attempt = 0
        while attempt < max_attempts:
            return_code = self.check_cluster_health()
            if  return_code == 0:
                return 0
            else:
                time.sleep(interval)
                attempt += 1
        if return_code == -1:
            print("FE is not ready")
        elif return_code == -2:
            print("BE is not ready")
        elif return_code == -3:
            print("mysql connection error")
        return -1

    def clean(self):
        """
        清理编译的输出/加载的数据/编译的缓存
        """
        self.clean_data()
        subprocess.run(["bash", "build.sh", "--clean"], cwd=self.build_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        output_dir = self.build_dir / "output"
        rm_dir(output_dir)
            
    # 删除数据be/fe
    def clean_data(self):
        rm_dir(self.fe_meta_dir)
        rm_dir(self.be_data_dir)
    
    def stop_services(self):
        """Stop frontend and backend services"""
        # 先关闭 FE，再关闭 BE
        fe_stop = os.path.join(self.build_dir, "output/fe/bin/stop_fe.sh")
        be_stop = os.path.join(self.build_dir, "output/be/bin/stop_be.sh")

        if os.path.exists(be_stop):
            subprocess.run(["bash", be_stop], cwd=self.build_dir, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(fe_stop):
            subprocess.run(["bash", fe_stop], cwd=self.build_dir, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
    
    def init_doris(self):
        """stop_services -> start_doris -> register_be_to_fe -> wait_cluster_ready"""
        self.stop_services()
        
        if self.start_doris() == -1:
            print("Doris start failed", flush=True)
            self.stop_services()
            return -1
        
        self.register_be_to_fe()
        if self.wait_cluster_ready() != 0:
            self.stop_services()
            print("Doris clusters are not alive", flush=True)
            return -1
            
        if get_folder_size(self.be_data_dir) < 0.3 * float(self.scale_factor) * 1024 * 1024:
            self.load_data()
        return 0
    
    def test(self, interval:int = 0, task:str = "hot") -> float:
        if task == "hot":
            return self.hot_test(interval)
        elif task == "cold":
            return self.cold_test(interval)
        else:
            raise ValueError("Invalid task. Use 'hot' or 'cold'.")
        
    
    def hot_test(self, interval:int) -> float:
        """gen_data(if not exists) -> load_data -> """
        """热测试：两次测试之间不停止服务，无间隔"""
        build_flag = False
        for _ in range(3):
            if self.init_doris() == 0:
                build_flag = True
                break
            
        if not build_flag:
            return -1
        
        successful_runs = []
        self.run_benchmark(interval=0, repeat=6) # warm up, 经验值
        for _ in range(self.repeat):
            _, current_time = self.run_benchmark(interval=interval, repeat=1)
            if current_time != -1:
                successful_runs.append(current_time)
            else:
                print("Error: 热测试失败")

        self.stop_services()

        if not successful_runs:
            print("Error: 热测试全部失败")
            return -1
        
        avg_time = sum(successful_runs) / len(successful_runs)
        return avg_time
        
    
    def cold_test(self, interval:int) -> float:
        """冷测试：两次测试之间停止服务，有间隔"""
        successful_runs = []
    
        for _ in range(self.repeat):
            self.stop_services()
            if self.init_doris() == -1:
                print("Error: 冷测试初始化失败")
                continue

            current_time, _ = self.run_benchmark(interval=interval, repeat=1)
            self.stop_services()

            if current_time != -1:
                successful_runs.append(current_time)
            else:
                print("Error: 冷测试失败")

        if not successful_runs:
            print("Error: 冷测试全部失败")
            return -1
        
        avg_time = sum(successful_runs) / len(successful_runs)
        return avg_time
            
    # build dbgen -> generate data
    def load_data(self) -> int:
        tpch_tools_path = self.tpch_home / "TPC-H_Tools_v3.0.0"
        tpch_data_path = self.tpch_home / "tpch-data"
        
        if not tpch_tools_path.exists():
            print("build tpch dbgen")
            subprocess.run(["bash", "build-tpch-dbgen.sh"], cwd=self.tpch_home, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if not tpch_data_path.exists():
            print("generating tpch data...")
            subprocess.run(["bash", "gen-tpch-data.sh", "-s", self.scale_factor], cwd=self.tpch_home, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        attempt, max_attempt = 0, 5
        while attempt < max_attempt:
            try:
                subprocess.run(["bash", "create-tpch-tables.sh", "-s", self.scale_factor], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, cwd=self.tpch_home)
                break
            except subprocess.CalledProcessError as e:
                print(f"Error: {e}")
                attempt += 1
                time.sleep(5)

        subprocess.run(["bash", "load-tpch-data.sh"], stdout=subprocess.DEVNULL, cwd=self.tpch_home)
    
    def run_benchmark(self, interval: float = 0, repeat:int = 3) -> tuple[float, float]:
        total_cold_time, total_hot_time = 0, 0
        for _ in range(repeat):
            try:
                process = subprocess.run(["bash", "run-tpch-queries.sh", "-s", self.scale_factor], stdout=subprocess.PIPE, cwd=self.tpch_home, text=True)
            except subprocess.CalledProcessError as e:
                print(f"Error: {e}")
                print("Error: subprocess failed")
                return (-1, -1)
            
            cold_time, hot_time = self.parse_result(process.stdout)
            if -1 in (cold_time, hot_time):
                return (-1,-1)
            print(f"Cold time: {cold_time}, Hot time: {hot_time}", flush=True)
            total_cold_time += cold_time
            total_hot_time += hot_time
            time.sleep(interval)
        return (total_cold_time/repeat, total_hot_time/repeat)
    
    def parse_result(self, result:str) -> tuple[float, float]:
        stdouts = result.splitlines()
        cold_time, hot_time = 0, 0
        for stdout in stdouts:
            if "Total cold run time" in stdout:
                cold_time = float(stdout.split(":")[1].replace("ms", "").strip())
            elif "Total hot run time" in stdout:
                hot_time = float(stdout.split(":")[1].replace("ms", "").strip())
        if cold_time == 0 or hot_time == 0:
            return (-1, -1)
        return (cold_time, hot_time)


def parallel_build(manager, manager_base, opt, opt_base=" -O3 "):
        with ThreadPoolExecutor(max_workers=4) as executor:
            future1 = executor.submit(manager.build, opt_config= opt)
            future2 = executor.submit(manager_base.build, opt_config=opt_base)

            result1 = future1.result()
            result2 = future2.result()
            return result1, result2

if __name__ == '__main__':
    # 在编译脚本时需要source /home/whq/dataset/doris/doris.env
    doris_manager_base = DorisManager(build_dir="/home/whq/dataset/doris",repeat=20)
    doris_manager = DorisManager(build_dir="/home/whq/dataset/dorisO3", repeat=20)

    parallel_build(doris_manager_base, doris_manager, '-O2')

    # 通常使用hot_test更合理一些
    print(f"the result of doris_manager_base: {doris_manager_base.test(interval=0, task='hot')}")
    print(f" the result of doris_manager: {doris_manager.test(interval=0, task='hot')}")