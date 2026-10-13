cmake_minimum_required(VERSION 3.20)
project(ext_cmm LANGUAGES CXX)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(VULKAN_HEADERS_DIR "${REPO_ROOT}/third_party/Vulkan-Headers/include" CACHE PATH "Directory containing recent vulkan/vulkan.h")
if(VULKAN_HEADERS_DIR)
    set(Vulkan_INCLUDE_DIR "${VULKAN_HEADERS_DIR}" CACHE PATH "Vulkan include directory" FORCE)
endif()
find_package(Vulkan REQUIRED)
find_program(GLSLANG_VALIDATOR NAMES glslangValidator glslang REQUIRED)
set(SPIRV_VAL "" CACHE FILEPATH "Optional maintenance1-capable spirv-val executable")

add_executable(ext_cmm main.cpp vulkan.cpp)
target_compile_features(ext_cmm PRIVATE cxx_std_17)
target_link_libraries(ext_cmm PRIVATE Vulkan::Vulkan)
target_compile_definitions(ext_cmm PRIVATE
    VULKAN_HPP_ENABLE_DYNAMIC_LOADER_TOOL=0
    DEMO_SHADER_DIR="${CMAKE_CURRENT_BINARY_DIR}/shaders")
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(ext_cmm PRIVATE -Wall -Wextra -Wpedantic)
endif()

file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/shaders")
foreach(shader kernel_fusion kernel_fusion_baseline)
    set(shader_source "${CMAKE_CURRENT_SOURCE_DIR}/shaders/${shader}.comp")
    set(shader_output "${CMAKE_CURRENT_BINARY_DIR}/shaders/${shader}.spv")
    set(validation_command)
    if(SPIRV_VAL)
        set(validation_command COMMAND "${SPIRV_VAL}" --target-env vulkan1.2 "${shader_output}")
    endif()
    add_custom_command(OUTPUT "${shader_output}"
        COMMAND "${GLSLANG_VALIDATOR}" -V --target-env vulkan1.2
                -o "${shader_output}" "${shader_source}"
        ${validation_command}
        DEPENDS "${shader_source}" "${GLSLANG_VALIDATOR}" ${SPIRV_VAL}
        VERBATIM)
    target_sources(ext_cmm PRIVATE "${shader_output}")
endforeach()
