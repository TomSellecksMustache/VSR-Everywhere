"""Public Open Model Zoo SR-1033 on Intel GPUs. Model stays resident across seeks."""
from __future__ import annotations

from pathlib import Path

from vsr_backends import intel_device, model_path


def build_model(core, path, width, height, output_width, output_height, strength):
    import numpy as np
    import openvino as ov
    from openvino import opset11 as op

    model = core.read_model(str(path))
    model.reshape({'0': [1, 3, height, width], '1': [1, 3, height * 3, width * 3]})
    image = op.parameter([1, height, width, 3], ov.Type.u8, name='rgb')
    bgr = op.transpose(op.gather(op.convert(image, 'f32'), op.constant([2, 1, 0]),
                                 op.constant(3)), op.constant([0, 3, 1, 2]))

    def resize(node, w, h):
        return op.interpolate(node, op.constant([h, w], np.int64), 'cubic',
                              'sizes', axes=op.constant([2, 3], np.int64),
                              coordinate_transformation_mode='half_pixel', cube_coeff=-0.75)

    # Match the publisher demo: BGR bytes and a bicubic byte image, output in 0..1.
    baseline = op.round(op.clamp(resize(bgr, width * 3, height * 3), 0, 255), 'half_to_even')
    model.input('0').replace(bgr.output(0))
    model.input('1').replace(baseline.output(0))
    enhanced = model.output(0).get_node().input_value(0)
    if strength < 1:
        enhanced = op.add(op.multiply(enhanced, op.constant(strength, np.float32)),
                          op.multiply(baseline, op.constant((1 - strength) / 255, np.float32)))
    enhanced = resize(enhanced, output_width, output_height)
    rgb = op.gather(enhanced, op.constant([2, 1, 0]), op.constant(1))
    pixels = op.convert(op.round(op.clamp(op.multiply(rgb, op.constant(255, np.float32)),
                                         0, 255), 'half_to_even'), 'u8')
    return ov.Model([op.transpose(pixels, op.constant([0, 2, 3, 1]))], [image], 'VSR Everywhere Intel')


class IntelSuperResolution:
    def __init__(self, width, height, output_width, output_height, strength, path=None):
        import openvino as ov
        self.core = ov.Core()
        self.device = intel_device(self.core)
        path = Path(path) if path else model_path()
        if not path.is_file() or not path.with_suffix('.bin').is_file():
            raise RuntimeError('Intel model is missing; run the VSR Everywhere installer again.')
        model = build_model(self.core, path, width, height, output_width, output_height, strength)
        self.compiled = self.core.compile_model(model, self.device, {'PERFORMANCE_HINT': 'LATENCY', 'NUM_STREAMS': '1'})
        self.request = self.compiled.create_infer_request()

    def run(self, rgb):
        # shortcut: Python OpenVINO/QSV interop uses host frames; use shared GPU surfaces when a portable API exists.
        self.request.infer({0: rgb[None]}, share_inputs=True)
        return self.request.get_output_tensor().data[0].tobytes()

    def close(self):
        self.request = self.compiled = self.core = None
