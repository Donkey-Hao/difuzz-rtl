import os
import sys
import cocotb
import random
import time

from cocotb.regression import TestFactory
from cocotb.decorators import coroutine
from cocotb.triggers import Timer, RisingEdge
from cocotb.result import TestError, TestFailure

@coroutine  # 这个关键词是装饰器，用于定义一个协程
def clock_gen(clock, period=2):
    while True:
        clock <= 1
        yield Timer(period/2)
        clock <= 0
        yield Timer(period/2)

@coroutine
def run_test(dut):
    cov = os.environ['COV']     # COV 是 coverage 的缩写
    assert cov in ['rand', 'mux', 'reg'], \
        'COV must be one of rand, mux, cov'

    chances = int(os.environ['CHANCES'])        # CHANCES 是机会数，可以理解为批次
    try: max_cycles = int(os.environ['MAX'])    # MAX 是最大周期数
    except: max_cycles = sys.maxint

    mutator = bitMutator()      # 变异器
    monitor = covMonitor(cov)   # 覆盖率监视器
    cocotb.fork(clock_gen(dut.clock))       # 生成时钟
    clkedge = RisingEdge(dut.clock)         # 时钟上升沿

    # num_iter = 1000

    last_cov = 0

    total_state_sum = 0
    bug_catch = 0
    total_cycle = 0
    start_time = time.time()
    for chance in range(chances):
        hit_bug = False
        # 每一批都要初始化变异器和监视器，这里变异器是随机的
        mutator.init()
        monitor.init()

        dut.meta_reset <= 1
        yield clkedge
        dut.meta_reset <= 0

        cycle = 0
        last_covsum = 0
        while cycle < max_cycles:
        # for iter1 in range(num_iter):
            in_bits = mutator.get_input()
            bits_list = []

            for i in range(0, len(in_bits), 3):
                bits_list.append(in_bits[i:i+3])

            dut.sdram_valid <= 0
            dut.flash_valid <= 0
            dut.rom_valid <= 0
            dut.sdram_data_i <= 0
            dut.flash_data_i <= 0
            dut.rom_data_i <= 0
            dut.reset <= 1
            yield clkedge
            dut.reset <= 0

            for bits in bits_list:
                sdram_valid = bits[0]
                # sdram_data = (bits[2] << 1 | bits[1]) & 0xf
                flash_valid = bits[1]
                # flash_data = (bits[7] << 3 | bits[6] << 2 | bits[5] << 1 | bits[4]) & 0xf
                rom_valid = bits[2]
                # rom_data = (bits[10] << 1 | bits[9]) & 0x3;
                dut.sdram_valid <= sdram_valid
                # dut.sdram_data_i <= sdram_data
                dut.flash_valid <= flash_valid
                # dut.flash_data_i <= flash_data
                dut.rom_valid <= rom_valid
                # dut.rom_data_i <= rom_data
                
                yield clkedge
                cycle = cycle + 1

                if (dut.io_cov_sum.value & 0xfffff) > last_covsum:
                    last_covsum = dut.io_cov_sum.value & 0xfffff
                    # print('{}, {}'.format(cycle, last_covsum))

                if cycle % 10000 == 0:
                    print('{}: {}'.format(cycle, last_covsum))

                # if (dut.bug.value & 0x1):
                #     hit_bug = True
                #     bug_catch = bug_catch + 1
                #     break

            # if hit_bug:
            #     break

            mytime = time.time() - start_time
            new, cov = monitor.interesting(dut.coverage.value & 0xfffff)
            if new:
                mutator.save_corpus()

        # if hit_bug:
        #     print('-------------------------------------------------------')
        #     # print('{}\t{}'.format(cycle, dut.io_cov_sum.value & 0xfffff))
        # else:
        #     print('Failed\t{}'.format(dut.io_cov_sum.value & 0xfffff))

        total_cycle = total_cycle + cycle
        total_state_sum = total_state_sum + (dut.io_cov_sum.value & 0xfffff)

    if bug_catch != 0:
        print('Average cycles to catch bug: {}, total state reached: {}'.format(total_cycle / bug_catch, total_state_sum))
    else:
        print('No bug found')


class bitMutator():
    def __init__(self):
        self.corpus = []    # 种子集合为空，即没有种子
        self.corpus_size = 100
        self.new_seed = None

    def init(self):
        self.corpus = []

    def get_input(self):
        # 50% 的概率使用随机种子，或者 corpus 为空时
        if not self.corpus or random.random() < 0.5:
            # 生成 30 位随机种子
            seed = [ random.randint(0,1) for i in range(3 * 10)]
        else:
            # 从 corpus 中随机选择一个种子
            seed = random.choice(self.corpus)
        # 变异种子
        self.new_seed = self.mutate(seed)
        return self.new_seed

    def mutate(self, seed):
        new_seed = []
        for i in range(len(seed)):
            # seed 的每一位有 20% 的概率变异
            if random.random() < 0.2:
                new_seed.append(1^seed[i])
            else:
                new_seed.append(seed[i])
        # 当 seed 的长度不足 30 时，有 10% 的概率增加 3 位
        if random.random() < 0.1 and len(new_seed) < 30:
            new_seed = new_seed + [ random.randint(0,1) for i in range(3)]
        # 当 seed 的长度超过 3 时，有 10% 的概率减少 3 位
        if random.random() < 0.1 and len(new_seed) > 3:
            new_seed = new_seed[0:len(new_seed) - 3]    # 从 0 开始，到 len(new_seed) - 3 结束

        return new_seed

    def save_corpus(self):
        self.corpus.append(self.new_seed)

class covMonitor():
    def __init__(self, cov):
        self.last_reg_cov = 0
        self.cov = cov
        self.mux_covs = []
        self.tot_covs = 0

    def init(self):
        self.last_reg_cov = 0
        self.mux_covs = []

    def interesting(self, coverage):
        # 只有在 cov 为 mux 和 reg 时，coverage 增加会被认为是有趣的
        # 这里的参数 coverage 是提高覆盖率的那个值（输入）
        if self.cov == 'mux':
            covsum = 0
            # 如果 coverage 不在 mux_covs 中，说明是新的覆盖
            if coverage not in self.mux_covs:
                self.mux_covs.append(coverage)
                # tot_covs 是所有覆盖的并集
                self.tot_covs = self.tot_covs | coverage
                # 计算 tot_covs 的二进制表示中 1 的个数
                for i in range(18):
                    covsum = covsum + (self.tot_covs >> i & 1)

                return True, covsum
            else:
                return False, covsum

        elif self.cov == 'reg':
            if coverage > self.last_reg_cov:
                self.last_reg_cov = coverage
                return True, coverage
            else:
                return False, coverage
        else:
            return False, 0

factory = TestFactory(run_test)
factory.generate_tests()
