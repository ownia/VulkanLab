#include "benchmark.hpp"
#include "vulkan.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using MatrixFeatures = vk::PhysicalDeviceCooperativeMatrixFeaturesKHR;
using MaintenanceFeatures = vk::PhysicalDeviceCooperativeMatrixMaintenance1FeaturesEXT;
using MemoryModelFeatures = vk::PhysicalDeviceVulkanMemoryModelFeatures;
using Unsupported = vklab::Unsupported;

struct Options {
    bool help = false;
    bool baselineOnly = false;
    bool queryOnly = false;
    bool matrices = false;
    uint32_t workgroups = 1024;
    uint32_t samples = 25;
};

uint32_t positiveCount(const std::string& value) {
    uint32_t count = 0;
    auto result = std::from_chars(value.data(), value.data() + value.size(), count);
    if (result.ec != std::errc{} || result.ptr != value.data() + value.size() || !count)
        throw std::runtime_error("Expected a positive integer, got: " + value);
    return count;
}

Options parseOptions(int argc, char** argv) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        std::string argument = argv[index];
        if (argument == "--help" || argument == "-h") options.help = true;
        else if (argument == "--baseline-only") options.baselineOnly = true;
        else if (argument == "--compare") options.baselineOnly = false;
        else if (argument == "--query-only") options.queryOnly = true;
        else if (argument == "--matrices") options.matrices = true;
        else if (argument == "--workgroups" && index + 1 < argc) options.workgroups = positiveCount(argv[++index]);
        else if (argument == "--samples" && index + 1 < argc) options.samples = positiveCount(argv[++index]);
        else throw std::runtime_error("Unknown option: " + argument);
    }
    return options;
}

void printHelp() {
    std::cout << "Usage: moltenvk_cmm [options]\n"
                 "  --baseline-only Run the KHR-only shader without requiring maintenance1\n"
                 "  --compare       Require and compare both shaders (default)\n"
                 "  --query-only    Query maintenance1 support without dispatching shaders\n"
                 "  --matrices      Print input and result matrices\n"
                 "  --workgroups N  Matrix tiles per timed dispatch (default: 1024)\n"
                 "  --samples N     Timed samples per shader after 5 warm-ups (default: 25)\n"
                 "  --help          Show this help\n"
                 "Exit codes: 0 = success, 1 = failure, 77 = unsupported/skipped\n";
}

struct Context {
    vk::raii::Context api{vkGetInstanceProcAddr};
    vk::raii::Instance instance{nullptr};
    vk::raii::PhysicalDevice physicalDevice{nullptr};
    vk::raii::Device device{nullptr};
    vk::raii::Queue queue{nullptr};
    uint32_t queueFamily = 0;
    uint32_t timestampBits = 0;
    uint32_t subgroupSize = 0;
    vk::PhysicalDeviceProperties properties;
    std::vector<vk::CooperativeMatrixProperties2EXT> configurations;
    std::vector<vk::CooperativeMatrixPropertiesKHR> khrConfigurations;
    bool baselineOnly;

    explicit Context(bool baseline) : baselineOnly(baseline) {
        vk::ApplicationInfo application("Cooperative Matrix Kernel Fusion Demo", 0, nullptr, 0, VK_API_VERSION_1_2);
        const char* instanceExtensions[] = {VK_KHR_PORTABILITY_ENUMERATION_EXTENSION_NAME};
        bool portability = vklab::hasExtension(api.enumerateInstanceExtensionProperties(), instanceExtensions[0]);
        instance = api.createInstance(vk::InstanceCreateInfo(portability ? vk::InstanceCreateFlagBits::eEnumeratePortabilityKHR :
            vk::InstanceCreateFlags{}, &application, 0, nullptr, portability ? 1 : 0, portability ? instanceExtensions : nullptr));
        for (auto& candidateDevice : instance.enumeratePhysicalDevices()) {
            auto candidate = candidateDevice.getProperties();
            auto extensions = candidateDevice.enumerateDeviceExtensionProperties();
            if (candidate.apiVersion >= VK_API_VERSION_1_2 &&
                vklab::hasExtension(extensions, VK_KHR_COOPERATIVE_MATRIX_EXTENSION_NAME) &&
                (baselineOnly || vklab::hasExtension(extensions, VK_EXT_COOPERATIVE_MATRIX_MAINTENANCE_1_EXTENSION_NAME))) {
                physicalDevice = std::move(candidateDevice);
                break;
            }
        }
        if (!*physicalDevice)
            throw Unsupported(baselineOnly ? "No device supports KHR cooperative matrices" :
                "Private MoltenVK does not support maintenance1; --compare cannot fall back to baseline");
        auto features = physicalDevice.getFeatures2<vk::PhysicalDeviceFeatures2, MatrixFeatures, MaintenanceFeatures, MemoryModelFeatures>();
        const auto& maintenance = features.get<MaintenanceFeatures>();
        if (!features.get<MatrixFeatures>().cooperativeMatrix || !features.get<MemoryModelFeatures>().vulkanMemoryModel ||
            (!baselineOnly && (!maintenance.cooperativeMatrixProperties2 || !maintenance.cooperativeMatrixPerElementOperations ||
            !maintenance.cooperativeMatrixReductions)))
            throw Unsupported("Softmax requires cooperative matrices, the Vulkan memory model, properties2, per-element operations, and reductions");
        auto deviceProperties = physicalDevice.getProperties2<vk::PhysicalDeviceProperties2,
            vk::PhysicalDeviceSubgroupProperties, vk::PhysicalDeviceCooperativeMatrixPropertiesKHR>();
        properties = deviceProperties.get<vk::PhysicalDeviceProperties2>().properties;
        const auto& subgroup = deviceProperties.get<vk::PhysicalDeviceSubgroupProperties>();
        subgroupSize = subgroup.subgroupSize;
        if (!(subgroup.supportedStages & vk::ShaderStageFlagBits::eCompute) ||
            !(deviceProperties.get<vk::PhysicalDeviceCooperativeMatrixPropertiesKHR>().cooperativeMatrixSupportedStages & vk::ShaderStageFlagBits::eCompute))
            throw Unsupported("Compute cooperative matrices are required");
        if (!subgroupSize || subgroupSize > properties.limits.maxComputeWorkGroupSize[0] ||
            subgroupSize > properties.limits.maxComputeWorkGroupInvocations)
            throw Unsupported("Default subgroup size cannot fit in one compute workgroup");
        khrConfigurations = physicalDevice.getCooperativeMatrixPropertiesKHR();
        if (!baselineOnly && !physicalDevice.getDispatcher()->vkGetPhysicalDeviceCooperativeMatrixProperties2EXT)
            throw std::runtime_error("Loader does not expose vkGetPhysicalDeviceCooperativeMatrixProperties2EXT");
        vk::PhysicalDeviceCooperativeMatrixInfo2EXT info;
        info.scope = vk::ScopeKHR::eSubgroup;
        info.subgroupSize = subgroupSize;
        if (!baselineOnly) configurations = physicalDevice.getCooperativeMatrixProperties2EXT(info);
        std::cout << "Device: " << properties.deviceName.data() << "\nSubgroup size: " << subgroupSize << "\n";
        std::cout << "KHR properties: " << khrConfigurations.size() << " configurations\n";
        if (!baselineOnly) std::cout << "Properties2: " << configurations.size() << " subgroup configurations\n";
    }

    void createDevice() {
        auto families = physicalDevice.getQueueFamilyProperties();
        auto found = std::find_if(families.begin(), families.end(), [](const auto& family) {
            return family.queueCount && (family.queueFlags & vk::QueueFlagBits::eCompute) && family.timestampValidBits;
        });
        if (found == families.end() || properties.limits.timestampPeriod <= 0)
            throw Unsupported("A timestamp-capable compute queue is required for this comparison");
        queueFamily = static_cast<uint32_t>(found - families.begin());
        timestampBits = found->timestampValidBits;
        vk::StructureChain<vk::DeviceCreateInfo, MatrixFeatures, MaintenanceFeatures, MemoryModelFeatures> enabled;
        enabled.get<MatrixFeatures>().cooperativeMatrix = true;
        if (baselineOnly) enabled.unlink<MaintenanceFeatures>();
        else {
            enabled.get<MaintenanceFeatures>().cooperativeMatrixProperties2 = true;
            enabled.get<MaintenanceFeatures>().cooperativeMatrixPerElementOperations = true;
            enabled.get<MaintenanceFeatures>().cooperativeMatrixReductions = true;
        }
        enabled.get<MemoryModelFeatures>().vulkanMemoryModel = true;
        float priority = 1.0f;
        vk::DeviceQueueCreateInfo queueInfo({}, queueFamily, 1, &priority);
        std::vector<const char*> extensions = {VK_KHR_COOPERATIVE_MATRIX_EXTENSION_NAME};
        if (!baselineOnly) extensions.push_back(VK_EXT_COOPERATIVE_MATRIX_MAINTENANCE_1_EXTENSION_NAME);
        if (vklab::hasExtension(physicalDevice.enumerateDeviceExtensionProperties(), "VK_KHR_portability_subset"))
            extensions.push_back("VK_KHR_portability_subset");
        auto& info = enabled.get<vk::DeviceCreateInfo>();
        info.setQueueCreateInfos(queueInfo).setPEnabledExtensionNames(extensions);
        device = physicalDevice.createDevice(info);
        queue = device.getQueue(queueFamily, 0);
    }
};

struct Shape {
    uint32_t rows;
    uint32_t columns;
};

Shape chooseShape(const Context& context) {
    if (context.subgroupSize != 32) throw Unsupported("This Metal experiment requires subgroup size 32");
    if (context.baselineOnly) {
        for (const auto& value : context.khrConfigurations)
            if (value.scope == vk::ScopeKHR::eSubgroup && value.MSize == 8 && value.NSize == 8 &&
                value.CType == vk::ComponentTypeKHR::eFloat32 && value.ResultType == vk::ComponentTypeKHR::eFloat32)
                return {8, 8};
        throw Unsupported("No KHR 8x8 FP32 accumulator configuration");
    }
    for (const auto& value : context.configurations) {
        uint32_t rows = value.MGranularity, columns = value.NGranularity;
        if (value.CType != vk::ComponentTypeKHR::eFloat32 || value.ResultType != vk::ComponentTypeKHR::eFloat32 ||
            !rows || !columns || rows > 256 || columns > 256 || uint64_t(rows) * columns > 4096 || columns % 4 != 0 ||
            uint64_t(rows) * columns * sizeof(float) > context.properties.limits.maxComputeSharedMemorySize)
            continue;
        return {rows, columns};
    }
    throw Unsupported("No small, aligned FP32 accumulator configuration");
}

std::vector<float> makeInput(const Shape& shape) {
    std::vector<float> values(size_t(shape.rows) * shape.columns);
    for (size_t index = 0; index < values.size(); ++index)
        values[index] = (static_cast<int32_t>(index % 17) - 8) * 0.5f;
    return values;
}

std::vector<float> makeBias(const Shape& shape) {
    std::vector<float> values(shape.columns);
    for (uint32_t column = 0; column < shape.columns; ++column)
        values[column] = (static_cast<float>(column) - 0.5f * static_cast<float>(shape.columns - 1)) * 0.5f;
    return values;
}

std::vector<float> cpuReference(const std::vector<float>& input, const std::vector<float>& bias) {
    std::vector<float> expected(input.size());
    for (size_t first = 0; first < input.size(); first += bias.size()) {
        float rowMaximum = -std::numeric_limits<float>::infinity();
        for (size_t column = 0; column < bias.size(); ++column) {
            expected[first + column] = 2.0f * input[first + column] + bias[column];
            rowMaximum = std::max(rowMaximum, expected[first + column]);
        }
        float rowSum = 0.0f;
        for (size_t column = 0; column < bias.size(); ++column) {
            expected[first + column] = std::exp(expected[first + column] - rowMaximum);
            rowSum += expected[first + column];
        }
        for (size_t column = 0; column < bias.size(); ++column)
            expected[first + column] /= rowSum;
    }
    return expected;
}

struct Execution {
    const vk::raii::Device& device;
    vklab::MappedStorageBuffer input, output, bias;
    vk::raii::DescriptorSetLayout descriptorLayout{nullptr};
    vk::raii::DescriptorPool descriptorPool{nullptr};
    vk::raii::DescriptorSet descriptor{nullptr};
    vk::raii::PipelineLayout pipelineLayout{nullptr};
    vk::raii::Pipeline pipeline{nullptr};
    vk::raii::QueryPool queryPool{nullptr};
    vk::raii::CommandPool commandPool{nullptr};
    vk::raii::CommandBuffer command{nullptr};
    vk::raii::Fence fence{nullptr};
    bool submitted = false;

    ~Execution() {
        if (submitted) vkDeviceWaitIdle(*device);
    }

    Execution(const Context& context, const Shape& shape, const std::vector<float>& values,
              const std::vector<float>& biasValues, const char* shaderName, uint32_t workgroups)
                : device(context.device), input(context.physicalDevice, context.device, values.size() * sizeof(float)),
                    output(context.physicalDevice, context.device, vk::DeviceSize(values.size()) * workgroups * sizeof(float)),
                    bias(context.physicalDevice, context.device, biasValues.size() * sizeof(float)) {
        std::memcpy(input.mapped, values.data(), static_cast<size_t>(input.bytes));
        std::memcpy(bias.mapped, biasValues.data(), static_cast<size_t>(bias.bytes));
        std::fill_n(static_cast<float*>(output.mapped), static_cast<size_t>(output.bytes / sizeof(float)),
                    std::numeric_limits<float>::quiet_NaN());
                input.flush();
                output.flush();
                bias.flush();
        const vk::DescriptorSetLayoutBinding bindings[] = {
            {0, vk::DescriptorType::eStorageBuffer, 1, vk::ShaderStageFlagBits::eCompute},
            {1, vk::DescriptorType::eStorageBuffer, 1, vk::ShaderStageFlagBits::eCompute},
            {2, vk::DescriptorType::eStorageBuffer, 1, vk::ShaderStageFlagBits::eCompute}
        };
        descriptorLayout = device.createDescriptorSetLayout(vk::DescriptorSetLayoutCreateInfo({}, bindings));
        vk::DescriptorPoolSize poolSize(vk::DescriptorType::eStorageBuffer, 3);
        descriptorPool = device.createDescriptorPool(vk::DescriptorPoolCreateInfo(vk::DescriptorPoolCreateFlagBits::eFreeDescriptorSet, 1, poolSize));
        descriptor = std::move(device.allocateDescriptorSets(vk::DescriptorSetAllocateInfo(*descriptorPool, *descriptorLayout)).front());
        const vk::DescriptorBufferInfo buffers[] = {
            {*input.buffer, 0, input.bytes}, {*output.buffer, 0, output.bytes}, {*bias.buffer, 0, bias.bytes}
        };
        const vk::WriteDescriptorSet writes[] = {
            {*descriptor, 0, 0, 1, vk::DescriptorType::eStorageBuffer, nullptr, &buffers[0]},
            {*descriptor, 1, 0, 1, vk::DescriptorType::eStorageBuffer, nullptr, &buffers[1]},
            {*descriptor, 2, 0, 1, vk::DescriptorType::eStorageBuffer, nullptr, &buffers[2]}
        };
        device.updateDescriptorSets(writes, {});
        pipelineLayout = device.createPipelineLayout(vk::PipelineLayoutCreateInfo({}, *descriptorLayout));
        auto words = vklab::readSpirvFile(std::string(DEMO_SHADER_DIR) + "/" + shaderName + ".spv");
        auto shader = device.createShaderModule(vk::ShaderModuleCreateInfo({}, words.size() * sizeof(uint32_t), words.data()));
        const uint32_t constants[] = {context.subgroupSize, shape.rows, shape.columns};
        const vk::SpecializationMapEntry entries[] = {{0, 0, sizeof(uint32_t)}, {1, 4, sizeof(uint32_t)}, {2, 8, sizeof(uint32_t)}};
        vk::SpecializationInfo specialization(3, entries, sizeof(constants), constants);
        vk::PipelineShaderStageCreateInfo stage({}, vk::ShaderStageFlagBits::eCompute, *shader, "main", &specialization);
        pipeline = device.createComputePipeline(nullptr, vk::ComputePipelineCreateInfo({}, stage, *pipelineLayout));
        queryPool = device.createQueryPool(vk::QueryPoolCreateInfo({}, vk::QueryType::eTimestamp, 2));
        commandPool = device.createCommandPool(vk::CommandPoolCreateInfo({}, context.queueFamily));
        command = std::move(device.allocateCommandBuffers(vk::CommandBufferAllocateInfo(*commandPool, vk::CommandBufferLevel::ePrimary, 1)).front());
        fence = device.createFence({});
        command.begin({});
        command.resetQueryPool(*queryPool, 0, 2);
        vk::MemoryBarrier before(vk::AccessFlagBits::eHostWrite | vk::AccessFlagBits::eShaderWrite,
                                 vk::AccessFlagBits::eShaderRead | vk::AccessFlagBits::eShaderWrite);
        command.pipelineBarrier(vk::PipelineStageFlagBits::eHost | vk::PipelineStageFlagBits::eComputeShader,
                                 vk::PipelineStageFlagBits::eComputeShader, {}, before, {}, {});
        command.bindPipeline(vk::PipelineBindPoint::eCompute, *pipeline);
        command.bindDescriptorSets(vk::PipelineBindPoint::eCompute, *pipelineLayout, 0, *descriptor, {});
        command.writeTimestamp(vk::PipelineStageFlagBits::eTopOfPipe, *queryPool, 0);
        command.dispatch(workgroups, 1, 1);
        command.writeTimestamp(vk::PipelineStageFlagBits::eBottomOfPipe, *queryPool, 1);
        vk::MemoryBarrier after(vk::AccessFlagBits::eShaderWrite, vk::AccessFlagBits::eHostRead);
        command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eHost, {}, after, {}, {});
        command.end();
    }
};

double timedDispatch(const Context& context, Execution& resources) {
    context.device.resetFences(*resources.fence);
    vk::SubmitInfo submit;
    submit.setCommandBuffers(*resources.command);
    context.queue.submit(submit, *resources.fence);
    resources.submitted = true;
    if (context.device.waitForFences(*resources.fence, true, std::numeric_limits<uint64_t>::max()) != vk::Result::eSuccess)
        throw std::runtime_error("Dispatch fence wait failed");
    resources.submitted = false;
    auto result = resources.queryPool.getResults<uint64_t>(0, 2, 2 * sizeof(uint64_t), sizeof(uint64_t),
                                                         vk::QueryResultFlagBits::e64 | vk::QueryResultFlagBits::eWait);
    if (result.result != vk::Result::eSuccess) throw std::runtime_error("Timestamp queries are unavailable");
    const auto& timestamps = result.value;
    double milliseconds = vklab::timestampMilliseconds(timestamps[0], timestamps[1], context.timestampBits,
                                                      context.properties.limits.timestampPeriod);
    if (!milliseconds) throw std::runtime_error("Timestamp resolution is insufficient; increase --workgroups");
    return milliseconds;
}

void printMatrix(const char* name, const float* values, uint32_t rows, uint32_t columns) {
    std::cout << "\n" << name << " (" << rows << "x" << columns << "):\n" << std::right;
    for (uint32_t row = 0; row < std::min(rows, 8u); ++row) {
        std::cout << "  ";
        for (uint32_t column = 0; column < std::min(columns, 8u); ++column)
            std::cout << std::setw(12) << values[size_t(row) * columns + column];
        if (columns > 8) std::cout << "  ...";
        std::cout << '\n';
    }
    if (rows > 8) std::cout << "  ...\n";
    std::cout << std::left;
}

void verifyResult(const char* name, Execution& resources, const std::vector<float>& expected, const Shape& shape) {
    resources.output.invalidate();
    auto actual = static_cast<const float*>(resources.output.mapped);
    size_t elements = static_cast<size_t>(resources.output.bytes / sizeof(float));
    float maximumError = 0.0f;
    for (size_t index = 0; index < elements; ++index) {
        size_t tileIndex = index % expected.size();
        float error = std::abs(actual[index] - expected[tileIndex]);
        if (!std::isfinite(actual[index]) || actual[index] < 0.0f || actual[index] > 1.0f ||
            error > 1e-6f + 1e-4f * std::abs(expected[tileIndex]))
            throw std::runtime_error(std::string(name) + " mismatch in tile " + std::to_string(index / expected.size()) +
                " at [" + std::to_string(tileIndex / shape.columns) + "," + std::to_string(tileIndex % shape.columns) +
                "]: expected " + std::to_string(expected[tileIndex]) + ", got " + std::to_string(actual[index]));
        maximumError = std::max(maximumError, error);
    }
    for (size_t first = 0; first < elements; first += shape.columns) {
        float rowSum = 0.0f;
        for (uint32_t column = 0; column < shape.columns; ++column) rowSum += actual[first + column];
        if (std::abs(rowSum - 1.0f) > 1e-4f)
            throw std::runtime_error(std::string(name) + " softmax row does not sum to one");
    }
    std::cout << "PASS  " << name << " (" << elements << " FP32 probabilities; max abs error " << maximumError
              << "; every row sums to one)\n";
}

void printTiming(const char* name, const vklab::Timing& timing) {
    std::cout << std::fixed << std::setprecision(6) << std::left << std::setw(20) << name
              << timing.median << " ms median  [" << timing.minimum << ", " << timing.maximum << "] min/max\n";
}

int runKernelFusion(Context& context, const Shape& shape, const Options& options) {
    context.createDevice();
    if (options.workgroups > context.properties.limits.maxComputeWorkGroupCount[0])
        throw std::runtime_error("--workgroups exceeds maxComputeWorkGroupCount[0]");
    auto input = makeInput(shape);
    auto bias = makeBias(shape);
    auto expected = cpuReference(input, bias);
    if (options.baselineOnly) {
        Execution baseline(context, shape, input, bias, "kernel_fusion_baseline", options.workgroups);
        for (unsigned warmup = 0; warmup < 5; ++warmup) timedDispatch(context, baseline);
        verifyResult("KHR-only baseline", baseline, expected, shape);
        std::vector<double> times(options.samples);
        for (auto& time : times) time = timedDispatch(context, baseline);
        verifyResult("KHR-only baseline after timing", baseline, expected, shape);
        printTiming("KHR-only baseline", vklab::summarize(times));
        if (options.matrices) printMatrix("KHR-only output", static_cast<const float*>(baseline.output.mapped), shape.rows, shape.columns);
        return 0;
    }
    std::cout << "\nKernel fusion: Y = row_softmax(2 * X + column_bias)\n"
              << options.workgroups << " independent tiles per dispatch, one subgroup per workgroup\n"
                 "maintenance1: matrix row max/sum + callbacks, no shared-memory spill\n"
                 "KHR-only: cooperative matrix store to shared memory + barrier + row-wise softmax\n"
                 "Both variants use one dispatch and no intermediate storage buffer\n";
    if (options.matrices) {
        printMatrix("Input X", input.data(), shape.rows, shape.columns);
        printMatrix("Per-column bias", bias.data(), 1, shape.columns);
    }
    Execution maintenance1(context, shape, input, bias, "kernel_fusion", options.workgroups);
    Execution baseline(context, shape, input, bias, "kernel_fusion_baseline", options.workgroups);
    for (unsigned warmup = 0; warmup < 5; ++warmup) {
        timedDispatch(context, maintenance1);
        timedDispatch(context, baseline);
    }
    verifyResult("maintenance1", maintenance1, expected, shape);
    verifyResult("KHR-only baseline", baseline, expected, shape);
    if (options.matrices) {
        printMatrix("Maintenance1 output Y (first tile)", static_cast<const float*>(maintenance1.output.mapped), shape.rows, shape.columns);
        printMatrix("KHR-only output Y (first tile)", static_cast<const float*>(baseline.output.mapped), shape.rows, shape.columns);
    }
    std::vector<double> maintenanceTimes(options.samples), baselineTimes(options.samples);
    for (uint32_t sample = 0; sample < options.samples; ++sample) {
        if (sample % 2 == 0) {
            maintenanceTimes[sample] = timedDispatch(context, maintenance1);
            baselineTimes[sample] = timedDispatch(context, baseline);
        } else {
            baselineTimes[sample] = timedDispatch(context, baseline);
            maintenanceTimes[sample] = timedDispatch(context, maintenance1);
        }
    }
    auto maintenanceTiming = vklab::summarize(maintenanceTimes);
    auto baselineTiming = vklab::summarize(baselineTimes);
    verifyResult("maintenance1 after timing", maintenance1, expected, shape);
    verifyResult("KHR-only baseline after timing", baseline, expected, shape);
    std::cout << "\nVulkan timestamp comparison: 5 warm-ups, " << options.samples << " samples per shader, alternating order\n";
    printTiming("maintenance1", maintenanceTiming);
    printTiming("KHR-only baseline", baselineTiming);
    std::cout << std::setprecision(3) << "KHR / maintenance1 median ratio: " << baselineTiming.median / maintenanceTiming.median
              << "x (>1 favors maintenance1)\n"
                 "Pipeline creation, host submission, fence waits, and readback are outside the timestamp interval.\n"
                 "Results apply only to this private MoltenVK build and selected device.\n";
    return 0;
}

}

int main(int argc, char** argv) {
    try {
        Options options = parseOptions(argc, argv);
        if (options.help) { printHelp(); return 0; }
        if (!options.baselineOnly)
            std::cerr << "EXPERIMENTAL: 8x8 softmax subset, not a conformant maintenance1 implementation.\n";
        Context context(options.baselineOnly);
        Shape shape = chooseShape(context);
        std::cout << "Selected accumulator: " << shape.rows << "x" << shape.columns << " FP32\n";
        if (options.queryOnly) return 0;
        return runKernelFusion(context, shape, options);
    } catch (const Unsupported& error) {
        std::cerr << "SKIP  " << error.what() << '\n';
        return 77;
    } catch (const std::exception& error) {
        std::cerr << "FAIL  " << error.what() << '\n';
        return 1;
    }
}