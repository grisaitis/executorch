#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# Copyright 2023-2024 Arm Limited and/or its affiliates.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

set -eu



########
### Hardcoded constants
########
script_dir=$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)

# Default Ethos-u tool folder override with --scratch-dir=<FOLDER>
root_dir=${script_dir}/ethos-u-scratch

model_name=""
aot_arm_compiler_flags="--delegate --quantize"
target="ethos-u55-128"
output_folder_set=false
output_folder="."
build_only=false

help() {
    echo "Usage: $(basename $0) [options]"
    echo "Options:"
    echo "  --model_name=<MODEL>                   Model to run, can be a builtin, examples/models or a filename Default to all builtin models"
    echo "  --aot_arm_compiler_flags=<FLAGS>       Only used if --model_name is used Default: ${aot_arm_compiler_flags}"
    echo "  --target=<TARGET>                      Target to build and run for Default: ${target}"
    echo "  --output=<FOLDER>                      Output folder Default: ${output_folder}"
    echo "  --build_only                           Only build, don't run FVP"
    echo "  --scratch-dir=<FOLDER>                 Path to your Ethos-U scrach dir if you not using default"
    exit 0
}

for arg in "$@"; do
    case $arg in
      -h|--help) help ;;
      --model_name=*) model_name="${arg#*=}";;
      --aot_arm_compiler_flags=*) aot_arm_compiler_flags="${arg#*=}";;
      --target=*) target="${arg#*=}";;
      --output=*) output_folder="${arg#*=}" ; output_folder_set=true ;;
      --build_only) build_only=true ;;
      --scratch-dir=*) root_dir="${arg#*=}";;
      *)
      ;;
    esac
done

root_dir=$(realpath ${root_dir})
output_folder=$(realpath ${output_folder})
mkdir -p ${output_folder}
if [ "$output_folder_set" = true ] ; then
    executor_runner_path=${output_folder}
else
    executor_runner_path=${script_dir}/executor_runner
fi
executor_runner_path=$(realpath ${executor_runner_path})

ethos_u_root_dir="$(cd ${root_dir}/ethos-u && pwd)"
ethos_u_build_dir=${ethos_u_root_dir}/core_platform/build
setup_path_script=${root_dir}/setup_path.sh

# Executorch
et_root_dir=$(cd ${script_dir}/../.. && pwd)
et_build_dir=${et_root_dir}/cmake-out

fvp_model=FVP_Corstone_SSE-300_Ethos-U55
toolchain_cmake=${script_dir}/ethos-u-setup/arm-none-eabi-gcc.cmake
_setup_msg="please refer to ${script_dir}/ethos-u-setup/setup.sh to properly install necessary tools."

# Generate a pte file
function generate_pte_file() {
    [[ $# -ne 2 ]] && { echo "[${FUNCNAME[0]}]" "Expecting model and model_compiler_flags flag, got, $*"; exit 1; }
    local model=${1}
    local model_compiler_flags=${2}

    local model_filename=${model}_arm_${target}.pte
    if [[ "${model_compiler_flags}" == *"--delegate"* ]]; then
	# Name aligned with default aot_arm_compiler output - run.sh only supports
	# running on Corstone-300 with Ethos-U55 FVP at the moment.
        model_filename=${model}_arm_delegate_${target}.pte
    fi
    cd $et_root_dir

    local pte_file
    pte_file=$(realpath ${output_folder}/${model_filename})
    rm -f "${pte_file}"

    # We are using the aot_lib from build_quantization_aot_lib below
    SO_LIB=$(find cmake-out-aot-lib -name libquantized_ops_aot_lib.so)

    python3 -m examples.arm.aot_arm_compiler --model_name="${model}" --target=${target} ${model_compiler_flags} --output ${output_folder} --so_library="$SO_LIB" 1>&2
    [[ -f ${pte_file} ]] || { >&2 echo "Failed to generate a pte file - ${pte_file}"; exit 1; }
    echo "${pte_file}"
}

# Build .so library to register quant ops with AoT flow
function build_quantization_aot_lib()
{
    SITE_PACKAGES="$(python3 -c 'from distutils.sysconfig import get_python_lib; print(get_python_lib())')"
    CMAKE_PREFIX_PATH="${SITE_PACKAGES}/torch"

    cd $et_root_dir
    mkdir -p cmake-out-aot-lib
    cmake \
        -DCMAKE_BUILD_TYPE=Release \
        -DEXECUTORCH_BUILD_XNNPACK=OFF \
        -DEXECUTORCH_BUILD_KERNELS_QUANTIZED=ON \
        -DEXECUTORCH_BUILD_KERNELS_QUANTIZED_AOT=ON \
        -DCMAKE_PREFIX_PATH="$CMAKE_PREFIX_PATH" \
        -DPYTHON_EXECUTABLE=python3 \
        -Bcmake-out-aot-lib \
        "${et_root_dir}"

    n=$(nproc)
    cmake --build cmake-out-aot-lib -j"$((n - 5))" -- quantized_ops_aot_lib
}


# build ExecuTorch Libraries
function build_executorch() {
    set -x

    [[ -d "${et_build_dir}" ]] \
        && echo "[${FUNCNAME[0]}] Warn: using already existing build-dir for executorch: ${et_build_dir}!!"
    mkdir -p "${et_build_dir}"

    cd "${et_root_dir}"
    cmake                                                 \
        -DCMAKE_INSTALL_PREFIX=${et_build_dir}            \
        -DEXECUTORCH_BUILD_EXECUTOR_RUNNER=OFF            \
        -DCMAKE_BUILD_TYPE=Release                        \
        -DEXECUTORCH_ENABLE_LOGGING=ON                    \
        -DEXECUTORCH_BUILD_ARM_BAREMETAL=ON               \
        -DEXECUTORCH_BUILD_KERNELS_QUANTIZED=ON           \
        -DEXECUTORCH_BUILD_EXTENSION_RUNNER_UTIL=ON       \
        -DFLATC_EXECUTABLE="$(which flatc)"               \
        -DCMAKE_TOOLCHAIN_FILE="${toolchain_cmake}"       \
        -B${et_build_dir}                                 \
        "${et_root_dir}"

    echo "[${FUNCNAME[0]}] Configured CMAKE"

    n=$(nproc)
    cmake --build ${et_build_dir} -j"$((n - 5))" --target install --config Release

    cmake                                                 \
        -DCMAKE_INSTALL_PREFIX=${et_build_dir}            \
        -DCMAKE_BUILD_TYPE=Release                        \
        -DEXECUTORCH_SELECT_OPS_LIST="aten::_softmax.out" \
        -DEXECUTORCH_BUILD_ARM_BAREMETAL=ON               \
        -DCMAKE_TOOLCHAIN_FILE="${toolchain_cmake}"       \
        -B"${et_build_dir}"/examples/arm                  \
        "${et_root_dir}"/examples/arm
    cmake --build ${et_build_dir}/examples/arm -- -j"$((n - 5))"

    set +x

    cd "${et_build_dir}"
    echo "[${FUNCNAME[0]}] Generated static libraries for ExecuTorch:"
    find . -name "*.a" -exec ls -al {} \;
}

# build Arm Baremetal executor_runner
function build_executorch_runner() {
    echo "[${FUNCNAME[0]}] Generating ExecuTorch libraries"
    [[ $# -ne 1 ]] && { echo "[${FUNCNAME[0]}]" "Expecting a single pte file as argument got, $*"; exit 1; }
    local pte=${1}
    if [[ ${target} == *"ethos-u55"*  ]]; then
        local target_cpu=cortex-m55
    else
        local target_cpu=cortex-m85
    fi
    cd ${script_dir}/executor_runner
    cmake -DCMAKE_TOOLCHAIN_FILE=${toolchain_cmake} \
	  -DTARGET_CPU=${target_cpu}                    \
      -DETHOSU_TARGET_NPU_CONFIG=${target}          \
	  -B ${executor_runner_path}/cmake-out          \
	  -DETHOS_SDK_PATH:PATH=${ethos_u_root_dir}     \
	  -DET_DIR_PATH:PATH=${et_root_dir}             \
	  -DET_BUILD_DIR_PATH:PATH=${et_build_dir}      \
	  -DET_PTE_FILE_PATH:PATH="${pte}"              \
	  -DPYTHON_EXECUTABLE=$(which python3)
    echo "[${FUNCNAME[0]}] Configured CMAKE"

    n=$(nproc)
    cmake --build ${executor_runner_path}/cmake-out -- -j"$((n - 5))" arm_executor_runner
    echo "[${FUNCNAME[0]}] Generated baremetal elf file:"
    find ${executor_runner_path}/cmake-out -name "arm_executor_runner"
    echo "executable_text: $(find ${executor_runner_path}/cmake-out -name arm_executor_runner -exec size {} \; | grep -v filename | awk '{print $1}') bytes"
    echo "executable_data: $(find ${executor_runner_path}/cmake-out -name arm_executor_runner -exec size {} \; | grep -v filename | awk '{print $2}') bytes"
    echo "executable_bss:  $(find ${executor_runner_path}/cmake-out -name arm_executor_runner -exec size {} \; | grep -v filename | awk '{print $3}') bytes"
}

# Execute the executor_runner on FVP Simulator
function run_fvp() {
    [[ $# -ne 1 ]] && { echo "[${FUNCNAME[0]}]" "Expexted elf binary name, got $*"; exit 1; }
    local elf_name=${1}
    elf=$(find ${executor_runner_path} -name "${elf_name}")
    [[ ! -f $elf ]] && { echo "[${FUNCNAME[0]}]: Unable to find executor_runner elf: ${elf}"; exit 1; }
    num_macs=$(echo ${target} | cut -d - -f 3)
    if [[ ${target} == *"ethos-u55"*  ]]; then
        echo "Running ${elf} for ${target} run with FVP_Corstone_SSE-300_Ethos-U55 num_macs:${num_macs}"
        FVP_Corstone_SSE-300_Ethos-U55                          \
            -C cpu0.CFGITCMSZ=11                                \
            -C ethosu.num_macs=${num_macs}                      \
            -C mps3_board.visualisation.disable-visualisation=1 \
            -C mps3_board.telnetterminal0.start_telnet=0        \
            -C mps3_board.uart0.out_file='-'                    \
            -C mps3_board.uart0.shutdown_on_eot=1               \
            -a "${elf}"                                         \
            --timelimit 120 || true # seconds
        echo "[${FUNCNAME[0]} Simulation complete, $?"
    else
        echo "Running ${elf} for ${target} is not supported"
        exit 1
    fi
}

#######
### Main
#######
# Source the tools
# This should be prepared by the setup.sh
[[ -f ${setup_path_script} ]] \
    || { echo "Missing ${setup_path_script}. ${_setup_msg}"; exit 1; }
source ${root_dir}/setup_path.sh

# basic checks before we get started
hash ${fvp_model} \
    || { echo "Could not find ${fvp_model} on PATH, ${_setup_msg}"; exit 1; }

hash arm-none-eabi-gcc \
    || { echo "Could not find arm baremetal toolchain on PATH, ${_setup_msg}"; exit 1; }

[[ -f ${toolchain_cmake} ]] \
    || { echo "Could not find ${toolchain_cmake} file, ${_setup_msg}"; exit 1; }

[[ -f ${et_root_dir}/CMakeLists.txt ]] \
    || { echo "Executorch repo doesn't contain CMakeLists.txt file at root level"; exit 1; }

# build executorch libraries
build_executorch
build_quantization_aot_lib

if [[ -z "$model_name" ]]; then
    # the test models run, and whether to delegate
    test_model=( "softmax" "add" "add3" "mv2" )
    model_compiler_flags=( "" "--delegate" "--delegate" "--delegate --quantize" )
else
    test_model=( "$model_name" )
    model_compiler_flags=( "$aot_arm_compiler_flags" )
fi

# loop over running the AoT flow and executing the model on device
for i in "${!test_model[@]}"; do
    echo "--------------------------------------------------------------------------------"
    printf "Running e2e flow for model '%s' with flags '%s'\n" "${test_model[i]}" "${model_compiler_flags[i]}"
    echo "--------------------------------------------------------------------------------"
    pte=$(generate_pte_file "${test_model[i]}" "${model_compiler_flags[i]}")
    stat --printf="Generated pte_data_size: %s bytes\npte_file:%n\n" ${pte}
    if [[ ${target} == *"TOSA"*  ]]; then
        echo "Build for ${target} skip generating .elf and running"
    else
        # Rebuild the application as the pte is imported as a header/c array
        build_executorch_runner "${pte}"
        if [ "$build_only" = false ] ; then
            run_fvp arm_executor_runner
        fi
    fi
done

exit 0
