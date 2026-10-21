"""验收程序：只能作为文件交给真实 GPU 沙箱执行，不在宿主机导入。"""

import ctypes as ct
import json
import platform

PTX = b"""
.version 6.4
.target sm_70
.address_size 64
.visible .entry vector_add(.param .u64 a, .param .u64 b, .param .u64 output)
{
    .reg .b32 %index;
    .reg .b64 %address<7>;
    .reg .f32 %value<4>;
    mov.u32 %index, %tid.x;
    mul.wide.u32 %address0, %index, 4;
    ld.param.u64 %address1, [a];
    ld.param.u64 %address2, [b];
    ld.param.u64 %address3, [output];
    add.u64 %address4, %address1, %address0;
    add.u64 %address5, %address2, %address0;
    add.u64 %address6, %address3, %address0;
    ld.global.f32 %value1, [%address4];
    ld.global.f32 %value2, [%address5];
    add.f32 %value3, %value1, %value2;
    st.global.f32 [%address6], %value3;
    ret;
}
"""


class CudaDriver:
    def __init__(self):
        self.library = ct.CDLL("libcuda.so.1")

    def call(self, name, *arguments):
        result = getattr(self.library, name)(*arguments)
        if result:
            raise RuntimeError(f"{name} failed with actual CUDA error {result}")

    def add(self):
        module = ct.c_void_p()
        self.call("cuModuleLoadData", ct.byref(module), ct.c_char_p(PTX))
        buffers = []
        try:
            function = ct.c_void_p()
            self.call("cuModuleGetFunction", ct.byref(function), module, ct.c_char_p(b"vector_add"))
            values = [
                (ct.c_float * 4)(1, 2, 3, 4),
                (ct.c_float * 4)(10, 20, 30, 40),
                (ct.c_float * 4)(),
            ]
            size = ct.c_size_t(ct.sizeof(values[0]))
            for value in values:
                pointer = ct.c_uint64()
                self.call("cuMemAlloc_v2", ct.byref(pointer), size)
                buffers.append(pointer)
                self.call("cuMemcpyHtoD_v2", pointer, ct.cast(value, ct.c_void_p), size)
            # kernelParams 的每项指向保存 CUdeviceptr 的宿主变量，而不是直接传 GPU 地址。
            parameters = (ct.c_void_p * 3)(
                *[ct.cast(ct.byref(pointer), ct.c_void_p) for pointer in buffers]
            )
            self.call(
                "cuLaunchKernel",
                function,
                ct.c_uint(1),
                ct.c_uint(1),
                ct.c_uint(1),
                ct.c_uint(4),
                ct.c_uint(1),
                ct.c_uint(1),
                ct.c_uint(0),
                ct.c_void_p(),
                parameters,
                ct.c_void_p(),
            )
            self.call("cuCtxSynchronize")
            self.call("cuMemcpyDtoH_v2", ct.cast(values[2], ct.c_void_p), buffers[2], size)
            return list(values[2])
        finally:
            for pointer in buffers:
                self.call("cuMemFree_v2", pointer)
            self.call("cuModuleUnload", module)


def main():
    driver = CudaDriver()
    driver.call("cuInit", ct.c_uint(0))
    device = ct.c_int()
    driver.call("cuDeviceGet", ct.byref(device), ct.c_int(0))
    name = ct.create_string_buffer(128)
    driver.call("cuDeviceGetName", name, ct.c_int(128), device)
    context = ct.c_void_p()
    driver.call("cuDevicePrimaryCtxRetain", ct.byref(context), device)
    try:
        driver.call("cuCtxSetCurrent", context)
        values = driver.add()
        assert values == [11.0, 22.0, 33.0, 44.0], values
        print(
            json.dumps(
                {
                    "device": name.value.decode(),
                    "python": platform.python_version(),
                    "values": values,
                }
            )
        )
    finally:
        driver.call("cuCtxSetCurrent", ct.c_void_p())
        driver.call("cuDevicePrimaryCtxRelease", device)


if __name__ == "__main__":
    main()
