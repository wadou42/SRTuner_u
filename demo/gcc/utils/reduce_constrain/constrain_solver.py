import os
from z3 import *


def get_opt_negation(option: str) -> str:
    option = option.strip()
    if not option:
        return ""
    
    if '=' in option:
        return ''

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
            return negated_prefix + option[len(prefix):]
    return ""


class ConstrainsSolver:
    def __init__(self, constrains_file: str):
        self.option_constrains: list[list[str]] = []
        if os.path.exists(constrains_file):
            f = open(constrains_file)
            constrains_file_lines = f.readlines()
            f.close()
            for line in constrains_file_lines:
                line = line.strip()
                if line.startswith("-"):
                    self.option_constrains.append(line.split(" "))

    def solve(self, opt_config: dict[str, str]):
        # remove single option constraints, if both option and its negation are in config
        all_single_constrain = set()
        for constrain_list in self.option_constrains:
            if len(constrain_list) > 1:
                continue
            constrain = constrain_list[0]
            all_single_constrain.add(constrain)
            if get_opt_negation(constrain) in all_single_constrain:
                opt_config.pop(constrain, None)
                opt_config.pop(get_opt_negation(constrain), None)

        all_cond: list[BoolRef] = []
        constrained_option_bool_refs: dict[str, BoolRef] = dict()

        for opt_constrain in self.option_constrains:
            # skip the constrain if not all options are in config
            single_cond: list[BoolRef] = []
            options_in_config = [
                1 for option in opt_constrain
                if option in opt_config or get_opt_negation(option) in opt_config
            ]
            if len(options_in_config) != len(opt_constrain):
                continue
            
            
            for option in opt_constrain:
                if option not in constrained_option_bool_refs:
                    if option not in opt_config:
                        assert get_opt_negation(option) in opt_config
                        option = get_opt_negation(option)
                        z3_bv = Bool(option)
                        single_cond.append(Not(z3_bv))
                    else:
                        z3_bv = Bool(option)
                        single_cond.append(z3_bv)
                    constrained_option_bool_refs.setdefault(option, z3_bv)
                else:
                    if option not in opt_config:
                        option = get_opt_negation(option)
                        single_cond.append(Not(constrained_option_bool_refs[option]))
                    else:
                        single_cond.append(constrained_option_bool_refs[option])
            all_cond.append(And(single_cond))

        solver = Solver()
        solver.add(Not(Or(all_cond)))

        if solver.check() == sat:
            model = solver.model()
            for option in constrained_option_bool_refs:
                opt_config[option] = bool(model[constrained_option_bool_refs[option]])
            return opt_config
        else:
            # Print unsat constraints
            unsat_constraints = []
            for cond in all_cond:
                solver_unsat = Solver()
                solver_unsat.add(cond)
                if solver_unsat.check() == unsat:
                    unsat_constraints.append(cond)

            print("No valid configuration found under given constraints.")
            print("Unsatisfiable constraints:")
            for unsat in unsat_constraints:
                print(unsat)
            raise RuntimeError("No valid configuration found under given constraints.")
        

if __name__ == "__main__":
    
    
    solver = ConstrainsSolver("reduce_constrains/constrains/fast.txt")

    print("Before:", opt_config)
    fixed_config = solver.solve(opt_config)
    print("After:", fixed_config)