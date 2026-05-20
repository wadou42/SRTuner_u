# 约束约简脚本简述

本项目提供doris、redis、scann的**静态**编译约束获取脚本。
编译约束相关信息请参照技术方案中的“4.3.2 编译约束求解”的章节。

简单来说，编译约束是一组能够使**项目功能构建失败**的**最小**编译选项序列。
本项目提供这些编译约束的获取脚本。

具体来说，这些脚本的输入为编译失败的选项（但并非最简选项）
；脚本的输出是一个文件，内容为通过选项约简得到的编译约束。

脚本无参数，所有的输入及项目路径配置均硬编码在脚本内部。

# Autotunning 准备工具

简要说明：本仓库包含用于生成和精简编译器/程序运行参数约束的脚本。核心脚本位于 `prepare/reduce_constrains/reduce_constrain.py`，通过随机生成配置、执行构建与测试并对失败配置做二分/逐项精简，最终将有问题的约束追加到约束文件中。

## 目录结构（相关）
- prepare/reduce_constrains/reduce_constrain.py — 主运行脚本（OptReducer）
- prepare/reduce_constrains/constrains/ — 存放生成/追加的约束文件
- opt_list.txt — 可选项列表（每行一个；支持布尔选项和枚举选项如 `-foo=[a|b|c]`）
- managers/ — Manager 接口与工厂，用于 build/test 的封装

## 依赖
- Python 3.8+
- 项目中 Manager 实现依赖的构建/测试工具（请确保 managers 下的实现可用）

## 快速使用
在仓库根目录运行：
- 示例（默认会尝试运行 test）：
  python prepare\reduce_constrains\reduce_constrain.py --manager fast --opt_file opt_list.txt --constrains_file prepare\reduce_constrains\constrains\fast.txt --random_config_count 20 --min_perf 0.001 --max_perf 1000000 --pass_ratio 0.95 --run_tests

说明：
- --manager: 在 ManagerFactory 中注册的管理器名称（例如 fast、keydb 等）
- --opt_file: 选项列表文件路径
- --constrains_file: 输出/追加约束文件路径
- --random_config_count: 每轮随机配置数量
- --min_perf / --max_perf: 测试性能阈值（超出视为失败）
- --pass_ratio: 达到该通过率则停止
- --run_tests: 启用测试（脚本内有对应参数；请确保 main 中将该标志传入 OptReducer 构造函数以生效）

注意：OptReducer 类构造函数有 run_tests 成员，可在代码中直接设置以控制是否执行 test。命令行参数需与主函数传入构造器保持一致（当前代码中 parser 包含 --run_tests，但需要确保将其传递给 OptReducer）

## opt_list.txt 格式
- 布尔开关示例：`-fomit-frame-pointer`、`-fno-omit-frame-pointer`
- 枚举示例：`-march=[x86|arm|riscv]`
- 支持注释行（以 `#` 开头）和空行

## 工作流程概述
1. 随机生成若干配置（每个配置包含基础级别如 `-O3` 与其他开关/枚举值）。
2. 使用 Manager.build 构建；若失败则视为失败。
3. 若启用测试，调用 Manager.test 并用 min/max 作校验。
4. 对失败配置执行约束求解（ConstrainsSolver），再尝试减少（reduceFlags / reduceMore）找到导致失败的最小子集并追加到约束文件中。
5. 重复直到达到指定的通过率。

## 常见问题
- 如果脚本一直报选项未识别：检查 opt_list.txt 中是否包含需要的选项，或 get_opt_negation 对否定规则是否覆盖。
- 想跳过测试只做编译：在创建 OptReducer 时将 run_tests=False（或在 main 中把命令行参数正确传入构造器）
