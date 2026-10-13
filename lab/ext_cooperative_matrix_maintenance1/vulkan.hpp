#pragma once

#include <vulkan/vulkan_raii.hpp>

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace vklab {

class Unsupported : public std::runtime_error {
public:
	using std::runtime_error::runtime_error;
};

bool hasExtension(const std::vector<vk::ExtensionProperties>& extensions, const char* name);
std::vector<uint32_t> readSpirvFile(const std::string& path);

struct MappedStorageBuffer {
	const vk::raii::Device& device;
	vk::raii::DeviceMemory memory{nullptr};
	vk::raii::Buffer buffer{nullptr};
	void* mapped = nullptr;
	vk::DeviceSize bytes;
	bool coherent = false;

	MappedStorageBuffer(const vk::raii::PhysicalDevice& physicalDevice, const vk::raii::Device& device,
						vk::DeviceSize size);
	~MappedStorageBuffer();
	MappedStorageBuffer(const MappedStorageBuffer&) = delete;
	MappedStorageBuffer& operator=(const MappedStorageBuffer&) = delete;

	void flush();
	void invalidate();

private:
	void synchronize(bool invalidate);
};

}