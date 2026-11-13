// SPDX-License-Identifier: GPL-3.0-only
// Uses Veyra EnhanceGraph, Copyright Likely7 and Veyra contributors.
#include <windows.h>
#include <d3d12.h>
#include <wrl/client.h>
#include <io.h>
#include <fcntl.h>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>
#include <memory>
#include <cmath>
#include <cwchar>
extern "C" {
#include <libavutil/frame.h>
#include <libavutil/pixfmt.h>
}
#include "veyra/Log.h"
#include "veyra/RuntimePaths.h"
#include "veyra/gfx/D3D12DeviceContext.h"
#include "veyra/gfx/CommandSlotRing.h"
#include "veyra/pipeline/EnhanceGraph.h"

using Microsoft::WRL::ComPtr;
namespace {
#pragma pack(push, 1)
struct Request { uint32_t magic, bytes, reset, slot; double ptsMs; };
struct Reply { uint32_t magic, bytes; };
#pragma pack(pop)
static_assert(sizeof(Request) == 24 && sizeof(Reply) == 8);

void check(HRESULT value, const char* operation) {
    if (FAILED(value)) throw std::runtime_error(std::string(operation) + " failed: " + std::to_string(uint32_t(value)));
}
void write(const void* data, size_t bytes) {
    if (std::fwrite(data, 1, bytes, stdout) != bytes) throw std::runtime_error("Output pipe closed");
}

class Readback {
    ComPtr<ID3D12Resource> buffer_;
    ComPtr<ID3D12CommandAllocator> allocator_;
    ComPtr<ID3D12GraphicsCommandList> commands_;
    ComPtr<ID3D12Fence> fence_;
    HANDLE event_ = nullptr;
    uint64_t value_ = 0;
public:
    ~Readback() { if (event_) CloseHandle(event_); }
    std::vector<uint8_t> read(ID3D12Device* device, ID3D12CommandQueue* queue,
                              ID3D12Resource* texture, ID3D12Fence* ready, uint64_t readyValue) {
        const auto desc = texture->GetDesc();
        if (desc.Format != DXGI_FORMAT_R8G8B8A8_UNORM) throw std::runtime_error("Unexpected Veyra output format");
        D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
        UINT rows = 0; UINT64 rowBytes = 0, total = 0;
        device->GetCopyableFootprints(&desc, 0, 1, 0, &footprint, &rows, &rowBytes, &total);
        if (!buffer_) {
            D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_READBACK;
            D3D12_RESOURCE_DESC resource{};
            resource.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; resource.Width = total;
            resource.Height = 1; resource.DepthOrArraySize = 1; resource.MipLevels = 1;
            resource.SampleDesc.Count = 1; resource.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
            check(device->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &resource,
                D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&buffer_)), "Readback allocation");
            check(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator_)), "Copy allocator");
            check(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator_.Get(), nullptr,
                IID_PPV_ARGS(&commands_)), "Copy commands");
            check(commands_->Close(), "Initial copy close");
            check(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence_)), "Copy fence");
            event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
            if (!event_) throw std::runtime_error("Copy event creation failed");
        }
        check(allocator_->Reset(), "Copy allocator reset");
        check(commands_->Reset(allocator_.Get(), nullptr), "Copy command reset");
        D3D12_RESOURCE_BARRIER barrier{};
        barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        barrier.Transition.pResource = texture;
        barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_COMMON;
        barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
        commands_->ResourceBarrier(1, &barrier);
        D3D12_TEXTURE_COPY_LOCATION source{}, destination{};
        source.pResource = texture; source.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        destination.pResource = buffer_.Get(); destination.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        destination.PlacedFootprint = footprint;
        commands_->CopyTextureRegion(&destination, 0, 0, 0, &source, nullptr);
        std::swap(barrier.Transition.StateBefore, barrier.Transition.StateAfter);
        commands_->ResourceBarrier(1, &barrier);
        check(commands_->Close(), "Copy close");
        if (ready && readyValue) check(queue->Wait(ready, readyValue), "Graph dependency");
        ID3D12CommandList* lists[] = {commands_.Get()}; queue->ExecuteCommandLists(1, lists);
        check(queue->Signal(fence_.Get(), ++value_), "Copy signal");
        check(fence_->SetEventOnCompletion(value_, event_), "Copy completion event");
        if (WaitForSingleObject(event_, 30000) != WAIT_OBJECT_0) throw std::runtime_error("GPU readback timed out");
        void* mapped = nullptr; D3D12_RANGE range{0, size_t(total)};
        check(buffer_->Map(0, &range, &mapped), "Readback map");
        std::vector<uint8_t> pixels(size_t(desc.Width) * desc.Height * 4);
        for (UINT row = 0; row < desc.Height; ++row)
            std::memcpy(pixels.data() + row * size_t(desc.Width) * 4,
                        static_cast<const uint8_t*>(mapped) + footprint.Offset + row * footprint.Footprint.RowPitch,
                        size_t(desc.Width) * 4);
        D3D12_RANGE empty{0, 0}; buffer_->Unmap(0, &empty);
        return pixels;
    }
};
}

int wmain(int argc, wchar_t** argv) {
    _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY);
    veyra::Logger::instance().setConsoleEnabled(false);
    try {
        if (argc < 4 || argc > 5) throw std::runtime_error("Usage: VsrVeyraFilter width height animation|live_action|unknown [--bypass]");
        const auto dimension = [](const wchar_t* value) {
            size_t used = 0;
            const auto parsed = std::stoul(value, &used);
            if (used != std::wcslen(value) || parsed > 1920) throw std::runtime_error("Invalid input dimension");
            return unsigned(parsed);
        };
        const unsigned width = dimension(argv[1]), height = dimension(argv[2]);
        const std::wstring profile = argv[3];
        if (!width || !height || width > 1920 || height > 1080 || width % 2 || height % 2)
            throw std::runtime_error("Input must be even-sized SDR video up to 1920x1080");
        if (profile != L"animation" && profile != L"live_action" && profile != L"unknown") throw std::runtime_error("Invalid content profile");
        const bool bypass = argc == 5 && std::wstring(argv[4]) == L"--bypass";
        if (argc == 5 && !bypass) throw std::runtime_error("Unknown argument");
        const auto log = std::filesystem::temp_directory_path() / (L"VsrVeyra-" + std::to_wstring(GetCurrentProcessId()) + L".log");
        veyra::Logger::instance().openFile(log.wstring());
        veyra::gfx::D3D12DeviceContext context; veyra::Status status;
        veyra::gfx::DeviceContextDesc device; device.requiredVendorId = 0x10de;
        if (!context.initialize(device, status)) throw std::runtime_error("NVIDIA D3D12 initialization failed");
        veyra::gfx::CommandSlotRing ring;
        if (!ring.initialize(context.device(), context.directQueue(), context.fence(), context.fenceEvent(), 4, status))
            throw std::runtime_error("GPU command ring initialization failed");
        veyra::pipeline::EnhanceGraph graph(context, ring);
        veyra::pipeline::EnhanceGraphDesc desc;
        desc.runtimeAbsPath = veyra::runtime::localRuntimeDirectory().wstring();
        desc.sourceWidth = desc.workWidth = width; desc.sourceHeight = desc.workHeight = height;
        desc.enableSr = desc.enableFg = false; desc.enableNr = !bypass;
        desc.noFeatures = desc.noNgx = bypass;
        desc.nrTemporal = true; desc.enableNvofStandalone = !bypass;
        veyra::engine::NrSettings model;
        const bool conservative = profile != L"live_action";
        model.intensity = conservative ? 0.35f : 0.6f;
        model.tone = conservative ? 0.1f : 0.25f;
        model.structure = conservative ? 0.25f : 0.6f;
        desc.nrLayersModel.push_back(model);
        if (!graph.initialize(desc) || !graph.createViews())
            throw std::runtime_error("Veyra neural rendering initialization failed; inspect " + log.string());
        std::printf("{\"event\":\"ready\",\"format\":\"rgba8-srgb\"}\n"); std::fflush(stdout);
        std::vector<uint8_t> input(size_t(width) * height * 3 / 2);
        Readback readback;
        const auto releaseFrame = [](AVFrame* value) { av_frame_free(&value); };
        std::unique_ptr<AVFrame, decltype(releaseFrame)> frame(av_frame_alloc(), releaseFrame);
        if (!frame) throw std::runtime_error("Input frame allocation failed");
        Request request{};
        while (const auto bytes = std::fread(&request, 1, sizeof(request), stdin)) {
            if (bytes != sizeof(request) || request.magic != 0x56535246 || request.bytes != input.size() || request.reset > 1 || !std::isfinite(request.ptsMs))
                throw std::runtime_error("Invalid input frame header");
            if (std::fread(input.data(), 1, input.size(), stdin) != input.size()) throw std::runtime_error("Incomplete input frame");
            frame->format = AV_PIX_FMT_NV12; frame->width = int(width); frame->height = int(height);
            frame->data[0] = input.data(); frame->data[1] = input.data() + size_t(width) * height;
            frame->linesize[0] = frame->linesize[1] = int(width);
            frame->color_range = AVCOL_RANGE_MPEG; frame->colorspace = AVCOL_SPC_BT709; frame->color_trc = AVCOL_TRC_BT709;
            veyra::pipeline::EnhanceGraph::FrameOutputs output;
            if (!graph.process(frame.get(), request.ptsMs, request.reset != 0, output, request.slot, nullptr, nullptr, false))
                throw std::runtime_error("Veyra frame processing failed; inspect " + log.string());
            auto pixels = readback.read(context.device(), context.directQueue(), graph.videoFrameResource(output.videoSlot),
                                        output.videoFenceObject.Get(), output.videoFenceValue);
            Reply reply{0x56535252, uint32_t(pixels.size())}; write(&reply, sizeof(reply)); write(pixels.data(), pixels.size()); std::fflush(stdout);
        }
        ring.drainQueue(); graph.shutdown();
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "VSR VEYRA ERROR: %s\n", error.what());
        return 1;
    }
}
