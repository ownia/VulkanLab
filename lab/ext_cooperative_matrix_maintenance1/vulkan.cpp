#include "vulkan.hpp"

#include <algorithm>
#include <cstring>
#include <fstream>
#include <stdexcept>

namespace vklab {

bool hasExtension(const std::vector<vk::ExtensionProperties>& extensions, const char* name) {
    return std::any_of(extensions.begin(), extensions.end(), [&](const auto& extension) {
        return std::strcmp(extension.extensionName.data(), name) == 0;
    });
}

std::vector<uint32_t> readSpirvFile(const std::string& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream) throw std::runtime_error("Cannot open shader: " + path);
    auto length = stream.tellg();
    if (length <= 0 || static_cast<size_t>(length) % sizeof(uint32_t))
        throw std::runtime_error("Invalid SPIR-V file size: " + path);
    std::vector<uint32_t> words(static_cast<size_t>(length) / sizeof(uint32_t));
    stream.seekg(0);
    stream.read(reinterpret_cast<char*>(words.data()), static_cast<std::streamsize>(length));
    if (!stream || words.front() != 0x07230203u) throw std::runtime_error("Invalid SPIR-V file: " + path);
    return words;
}

MappedStorageBuffer::MappedStorageBuffer(const vk::raii::PhysicalDevice& physicalDevice,
                                         const vk::raii::Device& device, vk::DeviceSize size)
    : device(device), bytes(size) {
    if (bytes > physicalDevice.getProperties().limits.maxStorageBufferRange)
        throw Unsupported("Storage buffer exceeds maxStorageBufferRange");
    buffer = device.createBuffer(vk::BufferCreateInfo({}, bytes, vk::BufferUsageFlagBits::eStorageBuffer));
    auto requirements = buffer.getMemoryRequirements();
    auto properties = physicalDevice.getMemoryProperties();
    uint32_t selected = properties.memoryTypeCount;
    for (unsigned pass = 0; pass < 2 && selected == properties.memoryTypeCount; ++pass) {
        for (uint32_t index = 0; index < properties.memoryTypeCount; ++index) {
            auto flags = properties.memoryTypes[index].propertyFlags;
            if ((requirements.memoryTypeBits & (1u << index)) && (flags & vk::MemoryPropertyFlagBits::eHostVisible) &&
                (pass || (flags & vk::MemoryPropertyFlagBits::eHostCoherent))) {
                selected = index;
                coherent = static_cast<bool>(flags & vk::MemoryPropertyFlagBits::eHostCoherent);
                break;
            }
        }
    }
    if (selected == properties.memoryTypeCount) throw Unsupported("No host-visible memory type for storage buffers");
    memory = device.allocateMemory(vk::MemoryAllocateInfo(requirements.size, selected));
    buffer.bindMemory(*memory, 0);
    mapped = memory.mapMemory(0, VK_WHOLE_SIZE);
}

MappedStorageBuffer::~MappedStorageBuffer() {
    if (mapped) memory.unmapMemory();
}

void MappedStorageBuffer::flush() {
    synchronize(false);
}

void MappedStorageBuffer::invalidate() {
    synchronize(true);
}

void MappedStorageBuffer::synchronize(bool invalidate) {
    if (coherent) return;
    vk::MappedMemoryRange range(*memory, 0, VK_WHOLE_SIZE);
    if (invalidate) device.invalidateMappedMemoryRanges(range);
    else device.flushMappedMemoryRanges(range);
}

}