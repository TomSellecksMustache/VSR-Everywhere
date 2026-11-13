cmake_minimum_required(VERSION 3.24)
project(VsrVeyraFilter LANGUAGES CXX)
if(NOT WIN32 OR NOT MSVC)
  message(FATAL_ERROR "The Veyra neural-rendering worker requires Windows x64 and MSVC")
endif()
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(VEYRA_ENABLE_EXPERIMENTAL_DLSSNR ON CACHE BOOL "" FORCE)
set(VEYRA_ENABLE_D3D12_DEBUG OFF CACHE BOOL "" FORCE)
set(VSR_VEYRA_FILTER_ONLY ON CACHE BOOL "" FORCE)
if(NOT VEYRA_SOURCE_DIR)
  message(FATAL_ERROR "Set VEYRA_SOURCE_DIR to the pinned, prepared Veyra source")
endif()
add_subdirectory("${VEYRA_SOURCE_DIR}" veyra)
target_sources(veyra_pipeline PRIVATE
  "${VEYRA_SOURCE_DIR}/src/beauty/OnnxLite.cpp"
  "${VEYRA_SOURCE_DIR}/src/beauty/TfliteLite.cpp"
  "${VEYRA_SOURCE_DIR}/src/beauty/YuNetGpu.cpp"
  "${VEYRA_SOURCE_DIR}/src/beauty/FaceMeshGpu.cpp"
  "${VEYRA_SOURCE_DIR}/src/beauty/FaceTracker.cpp"
  "${VEYRA_SOURCE_DIR}/src/pipeline/BeautyPass.cpp"
  "${VEYRA_SOURCE_DIR}/src/guidance/AmdOpticalFlow.cpp"
  "${VEYRA_SOURCE_DIR}/src/guidance/GpuDisOpticalFlow.cpp"
  "${VEYRA_SOURCE_DIR}/third_party/gpu-dis/src/gpu/native_strict_dis_provider.cpp")
target_include_directories(veyra_pipeline PRIVATE "${VEYRA_SOURCE_DIR}/third_party/gpu-dis/src/gpu")
add_executable(VsrVeyraFilter main.cpp)
target_compile_features(VsrVeyraFilter PRIVATE cxx_std_20)
target_compile_options(VsrVeyraFilter PRIVATE /utf-8 /W4)
target_compile_definitions(VsrVeyraFilter PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(VsrVeyraFilter PRIVATE "${VEYRA_FFMPEG_ROOT}/include" "${VEYRA_DLSS_SDK_ROOT}/include")
target_link_libraries(VsrVeyraFilter PRIVATE veyra_pipeline)
