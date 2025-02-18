# from typing import Optional, Dict, Tuple, Literal, Union, List, TypeVar, Hashable
import ctypes as _ctypes
import torch as _torch
import typing as _typing
import numpy as _np
import enum as _enum
from ._gmath import *
from ._enums import *


## TYPES SAME AS TORCH
dtype = _torch.dtype
float32: dtype = _torch.float32
t_float: dtype = _torch.float
float64: dtype = _torch.float64
double: dtype = _torch.double
float16: dtype = _torch.float16
bfloat16: dtype = _torch.bfloat16
half: dtype = _torch.half
uint8: dtype = _torch.uint8
int8: dtype = _torch.int8
int16: dtype = _torch.int16
short: dtype = _torch.short
int32: dtype = _torch.int32
t_int: dtype = _torch.int
int64: dtype = _torch.int64
ptr: dtype = _torch.int64
long: dtype = _torch.long
complex32: dtype = _torch.complex32
complex64: dtype = _torch.complex64
cfloat: dtype = _torch.cfloat
complex128: dtype = _torch.complex128
cdouble: dtype = _torch.cdouble
quint8: dtype = _torch.quint8
qint8: dtype = _torch.qint8
qint32: dtype = _torch.qint32
t_bool: dtype = _torch.bool
quint4x2: dtype = _torch.quint4x2


__DTYPE_TO_STR__ = {
        _torch.uint8: '|u1',
        _torch.float32: '<f4',
        float: '<f4',
        _torch.int32: '<i4',
        int: '<i4',
        _torch.int64: '<i8'
    }


__frozen_type_T = _typing.TypeVar('FrozenType')


def freezable_type(t: __frozen_type_T) -> __frozen_type_T:
    def new_call(cls, *args, **kwargs):
        instance = t(*args, **kwargs)
        instance._frozen = True
        return instance

    def new_setattr(self, key, value):
        if hasattr(self, '_frozen') and self._frozen:
            raise Exception('Object already frozen')
        object.__setattr__(self, key, value)

    t.__call__ = new_call
    t.__setattr__ = new_setattr
    return t


def mutable_method(f):
    def wrapper(self, *args, **kwargs):
        self._freeze = False
        result = f(self, *args, **kwargs)
        self._freeze = True
        return result
    return wrapper


class lazy_constant:
    def __init__(self, lazy_evaluation):
        self.lazy_evaluation = lazy_evaluation

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner):
        if instance is not None:
            value = self.lazy_evaluation(instance)
            object.__setattr__(instance, self.name, value)
        else:
            value = self.lazy_evaluation(owner)
            object.__setattr__(owner, self.name, value)
        return value
        # raise Exception('Wrong implementation')  # should never arrive here
        # for t in owner.__mro__:
        #     for k, v in t.__dict__.items():
        #         if v is self:  # found the descriptor in type
        #             value = self.lazy_evaluation(instance)
        #             object.__setattr__(instance, k, value)
        #             return value
        # raise Exception('Wrong implementation')  # should never arrive here


class wrap_cuda_ptr:
    def __init__(self, ptr, new_shape, new_type, strides = None):
        self.__cuda_array_interface__ = {
            'data': (ptr, False),
            'shape': new_shape,
            'typestr': __DTYPE_TO_STR__[new_type],
            'strides': strides,
            'version': 2
        }


class wrap_cpu_ptr:
    __array_interface__ = None
    def __init__(self, ptr, new_shape, new_type, strides = None):
        self.__array_interface__ = {
            'data': (ptr, False),
            'shape': new_shape,
            'typestr': __DTYPE_TO_STR__[new_type],
            'strides': strides,
            'version': 3
        }


class ViewTensor(_torch.Tensor):
    def __init__(self, *args):
        super(ViewTensor, self).__init__()
        self.memory_owner = None

    def __deepcopy__(self, memodict={}):
        t = super(ViewTensor, self).__deepcopy__(memodict)
        t.memory_owner = self.memory_owner
        return t

    def __getitem__(self, item):
        t = super(ViewTensor, self).__getitem__(item)
        if isinstance(t, _torch.Tensor):
            t = ViewTensor(t)
            t.memory_owner = self.memory_owner
        return t

    def detach(self):
        t = ViewTensor(super().detach())
        t.memory_owner = self.memory_owner
        return t

    def view(self, *shape) -> 'ViewTensor':
        t = ViewTensor(super(ViewTensor, self).view(*shape))
        t.memory_owner = self.memory_owner
        return t

    __torch_function__ = _torch._C._disabled_torch_function_impl

    @staticmethod
    def from_blob(ptr: int, shape: _typing.Union[_typing.Tuple[int], _typing.List[int]], dtype: _torch.dtype, device: _torch.device, *, strides: _typing.Union[_typing.Tuple[int], _typing.List[int], None] = None, owner: object = None) -> 'ViewTensor':
        if device.type == 'cpu':
            nptype = {
                _torch.int: _np.int32,
                _torch.float: _np.float32,
                _torch.uint8: _np.uint8,
                _torch.int64: _np.int64
            }[dtype]
            # wrap as cpu pointer
            data = _np.array(wrap_cpu_ptr(ptr, shape, dtype, strides=strides), dtype=nptype, copy=False)
            v = ViewTensor(_torch.from_numpy(data))
        elif device.type == 'cuda':
            data = wrap_cuda_ptr(ptr, shape, dtype, strides=strides)
            v = ViewTensor(_torch.as_tensor(data, dtype=dtype, device=device))
        else:
            raise Exception("Not supported tensor device")
        v.memory_owner = owner
        return v

    @staticmethod
    def reinterpret(t: _torch.Tensor, dtype: _typing.Union[_torch.dtype, int, float]):
        if dtype == int:
            dtype = _torch.int32
        if dtype == float:
            dtype = _torch.float32
        new_size = Layout.scalar_size(dtype)
        old_size = Layout.scalar_size(t.dtype)
        assert t.storage_offset() * old_size % new_size == 0, 'New type can not be aligned to base tensor'
        bytes_strides = tuple([t.stride(d) * old_size for d in range(len(t.shape))])
        assert all(b % new_size == 0 for b in bytes_strides[:-1]), 'Some stride is not multiple of the new type size'
        new_shape = t.shape[:-1]+(t.shape[-1] * old_size // new_size,)
        bytes_strides = bytes_strides[:-1] + (new_size,)  # update stride of last dimension to the new size
        return ViewTensor.from_blob(t.data_ptr(), new_shape, dtype, device=t.device, strides=bytes_strides, owner=t)


class LayoutAlignment(_enum.IntEnum):
    """
    Determines the type of rules for alignment applied in a layout.
    """
    COMPACT = 0,
    """
    No alignment is applied.
    """
    SCALAR = 1,
    """
    Scalar alignment is applied. Struct and arrays are aligned to the maximum scalar contained inside.
    """
    STD430 = 2,
    """
    Applies the std430 layout rules.
    """



@freezable_type
class Layout:
    """
    Represents a specific layout of fields and arrays in memory with alignment rules.
    """

    def __init__(self,
                 declaration,
                 size,
                 alignment: int = 1,
                 array_stride: _typing.Optional[int] = None,
                 matrix_stride: _typing.Optional[int] = None,
                 element_layout: _typing.Optional['Layout'] = None,
                 fields_layout: _typing.Optional[_typing.Dict[str, _typing.Tuple[int, 'Layout']]] = None):
        super(Layout, self).__init__()
        if declaration is int:
            declaration = _torch.int32
        if declaration is float:
            declaration = _torch.float32
        self.declaration = declaration
        self.size = size
        self.alignment = alignment
        self.aligned_size = Layout._align_size(size, alignment)
        self.array_stride = array_stride
        self.matrix_stride = matrix_stride
        self.stride = array_stride if array_stride is not None else matrix_stride
        self.element_layout = element_layout
        self.fields_layout = fields_layout
        self.is_scalar = array_stride is None and matrix_stride is None and element_layout is None and fields_layout is None
        self.is_array = array_stride is not None
        self.is_structure = fields_layout is not None
        self.is_vector = self.stride is None and element_layout is not None
        self.is_matrix = matrix_stride is not None
        self.is_compact = alignment == 1 and size > 1
        self.is_tensor_or_scalar = self.is_scalar or self.is_vector or self.is_matrix
        try:
            self.ctype = {
                int: _ctypes.c_int32,
                float: _ctypes.c_float,
                _torch.int: _ctypes.c_int32,
                _torch.float32: _ctypes.c_float,
                _torch.int64: _ctypes.c_int64,
            }[declaration]
        except:
            self.ctype = None
        try:
            self.scalar_format = {
                int: 'i',
                float: 'f',
                _torch.int: 'i',
                _torch.float32: 'f',
                _torch.int64: 'Q',
            }[declaration]
        except:
            self.scalar_format = 'B'

    @staticmethod
    def fix_shape(shape, total):
        """
        Converts a shape of a variable dimension (-1) to a fix shape.
        """
        shape = list(shape)
        num_el = 1
        free_pos = -1
        for i, s in enumerate(shape):
            assert s == -1 or s > 0, "Dimensions must be positive or -1 to indicate one of the dimensions as variable"
            if s == -1:
                assert free_pos == -1, "Not possible two variable dimensions"
                free_pos = i
            else:
                num_el *= s
        assert total % num_el == 0, "Dimensions are not valid combination for the number of elements of this buffer"
        if free_pos != -1:
            shape[free_pos] = total // num_el
        return tuple(shape)

    @staticmethod
    def _create_scalar_layout(type, size: int, alignment: int) -> 'Layout':
        return Layout(type, size, alignment)

    @staticmethod
    def _create_vector_layout(type, size: int, alignment: int, element_layout: 'Layout') -> 'Layout':
        return Layout(type, size, alignment, element_layout = element_layout)

    @staticmethod
    def _create_matrix_layout(type, size: int, alignment: int, element_layout: 'Layout', matrix_stride: int) -> 'Layout':
        return Layout(type, size, alignment, element_layout=element_layout, matrix_stride=matrix_stride)

    @staticmethod
    def _create_array_layout(type, size: int, alignment: int, element_layout: 'Layout', array_stride: int) -> 'Layout':
        return Layout(type, size, alignment, element_layout=element_layout, array_stride=array_stride)

    @staticmethod
    def _create_structure_layout(type, size: int, alignment: int, fields: _typing.Dict[str, _typing.Tuple[int, 'Layout']]) -> 'Layout':
        return Layout(type, size, alignment, fields_layout=fields)

    __TYPE_SIZES__ = {
        **{k: _torch.tensor([], dtype=k).element_size() for k in [
            t_float,
            float32,
            float64,
            double,
            float16,
            bfloat16,
            half,
            uint8,
            int8,
            int16,
            short,
            t_int,
            int32,
            int64,
            long,
            # complex32,
            complex64,
            cfloat,
            complex128,
            cdouble,
            # quint8,
            # qint32,
            # t_bool,
            # quint4x2
        ]},
        # int: 4,
        # float: 4,
        # complex: 16
    }

    __TYPE_FORMATS__ = {
        int: 'i',
        float: 'f',
        complex: 'f',
        float32: 'f',
        t_float: 'f',
        float64: 'd',
        double: 'd',
        float16: 'e',
        bfloat16: 'e',
        half: 'e',
        uint8: 'B',
        int8: 'b',
        int16: 'h',
        short: 'h',
        int32: 'i',
        t_int: 'i',
        int64: 'q',
        long: 'q',
        quint8: 'B',
        qint8: 'b',
        qint32: 'I',
        t_bool: '?',
        quint4x2: 'B'
    }

    @staticmethod
    def scalar_size(type: _typing.Union[type, _torch.dtype]) -> int:
        """
        Return the size in bytes for a scalar type, int, float, complex or torch dtypes.
        """
        if type == int or type == float:
            return 4
        if type == complex:
            return 16
        if type in Layout.__TYPE_SIZES__:
            return Layout.__TYPE_SIZES__[type]
        # if isinstance(type, GTensorMeta):
        #     return Layout.__TYPE_SIZES__[type.tensor_dtype] * math.prod(type.tensor_shape)
        raise Exception(f'Not supported type {type}')

    @staticmethod
    def _align_size(size, alignment):
        return (size + alignment - 1)//alignment * alignment

    @staticmethod
    def _build_layout_compact(type):
        if isinstance(type, _typing.Hashable) and type in Layout.__TYPE_SIZES__:
            size = Layout.__TYPE_SIZES__[type]
            return Layout._create_scalar_layout(type, size, 1)
        if isinstance(type, GTensorMeta):
            element_layout = Layout._build_layout_compact(type.tensor_dtype)
            assert element_layout.is_scalar, 'No supported matrices or vectors for non-scalar types'
            component_size = element_layout.size
            vec_size = type.tensor_shape[-1] * component_size
            vector_layout = Layout._create_vector_layout(type, vec_size, 1, element_layout)
            if type.dimension == 1:  # vector
                return vector_layout
            return Layout._create_matrix_layout(type, vec_size * type.tensor_shape[0], 1, vector_layout, vec_size)
        if isinstance(type, list):
            array_len = type[0]
            array_type = type[1]
            element_layout = Layout._build_layout_compact(array_type)
            return Layout._create_array_layout(
                type, array_len*element_layout.size, 1,
                element_layout, element_layout.size)
        if isinstance(type, dict):
            offset = 0
            fields = dict()
            for f, field_type in type.items():
                if f != '__name__':
                    field_layout = Layout._build_layout_compact(field_type)
                    fields[f] = (offset, field_layout)
                    offset += field_layout.size
            return Layout._create_structure_layout(type, offset, 1, fields)
        raise Exception('Not supported type definition')

    @staticmethod
    def _build_layout_scalar(type):
        if type == int or type == float:
            return Layout._create_scalar_layout(type, 4, 4)
        if isinstance(type, _typing.Hashable) and type in Layout.__TYPE_SIZES__:
            size = Layout.__TYPE_SIZES__[type]
            return Layout._create_scalar_layout(type, size, size)
        if isinstance(type, GTensorMeta):
            element_layout = Layout._build_layout_scalar(type.tensor_dtype)
            assert element_layout.is_scalar, 'No supported matrices or vectors for non-scalar types'
            component_size = element_layout.size
            vec_size = type.tensor_shape[-1] * component_size
            vector_layout = Layout._create_vector_layout(type, vec_size, component_size, element_layout)
            if type.dimension == 1:  # vector
                return vector_layout
            return Layout._create_matrix_layout(type, vec_size * type.tensor_shape[0], component_size, element_layout, vec_size)
        if isinstance(type, list):
            array_len = type[0]
            array_type = type[1]
            element_layout = Layout._build_layout_scalar(array_type)
            array_stride = Layout._align_size(element_layout.size, element_layout.alignment)
            return Layout._create_array_layout(type,
                                               array_len * array_stride,
                                               element_layout.alignment, element_layout,
                                               array_stride)
        if isinstance(type, dict):
            offset = 0
            fields = dict()
            max_alignment = 1
            for f, field_type in type.items():
                if f != '__name__':
                    field_layout = Layout._build_layout_scalar(field_type)
                    field_alignment = field_layout.alignment
                    offset = Layout._align_size(offset, field_alignment)
                    max_alignment = max(max_alignment, field_alignment)
                    fields[f] = (offset, field_layout)
                    offset += field_layout.size
            return Layout._create_structure_layout(type, offset, max_alignment, fields)
        raise Exception(f'Not supported type definition {type}')

    @staticmethod
    def _build_layout_std430(type):
        if type == int or type == float:
            return Layout._create_scalar_layout(type, 4, 4)
        if isinstance(type, _typing.Hashable) and type in Layout.__TYPE_SIZES__:
            size = Layout.__TYPE_SIZES__[type]
            return Layout._create_scalar_layout(type, size, size)
        if isinstance(type, GTensorMeta):
            element_layout = Layout._build_layout_std430(type.tensor_dtype)
            assert element_layout.is_scalar, 'No supported matrices or vectors for non-scalar types'
            component_size = element_layout.size
            shape = [*type.tensor_shape]
            if shape[-1] == 3:
                shape[-1] = 4
            vec_size = type.tensor_shape[-1] * component_size
            vec_align = shape[-1] * component_size
            vector_layout = Layout._create_vector_layout(type, vec_size, vec_align, element_layout)
            if type.dimension == 1:  # vector
                return vector_layout
            return Layout._create_matrix_layout(type, vec_align * type.tensor_shape[0], vec_align, element_layout, vec_align)
        if isinstance(type, list):
            array_len = type[0]
            array_type = type[1]
            element_layout = Layout._build_layout_std430(array_type)
            array_stride = Layout._align_size(element_layout.size, element_layout.alignment)
            return Layout._create_array_layout(type,
                                               array_len * array_stride,
                                               element_layout.alignment, element_layout,
                                               array_stride)
        if isinstance(type, dict):
            offset = 0
            fields = dict()
            max_alignment = 1
            for f, field_type in type.items():
                if f != '__name__':
                    field_layout = Layout._build_layout_std430(field_type)
                    field_alignment = field_layout.alignment
                    offset = Layout._align_size(offset, field_alignment)
                    max_alignment = max(max_alignment, field_alignment)
                    fields[f] = (offset, field_layout)
                    offset += field_layout.size
            return Layout._create_structure_layout(type, offset, max_alignment, fields)
        raise Exception('Not supported type definition')

    @staticmethod
    def from_description(mode: LayoutAlignment, description: _typing.Union[type, dict, list, _torch.dtype]) -> 'Layout':
        """
        Creates a layout from a general type definition.
        type definition defines:
        - arrays with a list in the form [<type>, size]
        - structures with a dict in the form { field_name : field_type, ... }
        - scalars with the types from _torch.dtype or int, float, complex.

        Example
        -------
        >>> my_vertex_layout = Layout.from_description(
        >>>     LayoutAlignment.SCALAR,
        >>>     dict(
        >>>         P=vec3,
        >>>         N=vec3
        >>>     )
        >>> )
        >>> my_ssbo_layout = Layout.from_description(
        >>>     LayoutAlignment.STD430,
        >>>     dict(
        >>>         count=int,
        >>>         data=[100, dict(P=vec3, C=vec4)]
        >>>     )
        >>> )
        """
        return {
            LayoutAlignment.COMPACT: Layout._build_layout_compact,
            LayoutAlignment.STD430: Layout._build_layout_std430,
            LayoutAlignment.SCALAR: Layout._build_layout_scalar
        }[mode](description)

    @staticmethod
    def from_structure(mode: LayoutAlignment = LayoutAlignment.SCALAR, **fields):
        """
        Creates a layout for a structure.

        Example
        -------
        >>> vertex_layout = Layout.from_structure(P=vec3, N=vec3)
        """
        return Layout.from_description(mode=mode, description={**fields})

    @staticmethod
    def from_instance():
        """
        Creates a layout for an instance buffer
        """
        return Layout.from_structure(
            mode=LayoutAlignment.SCALAR,
            # transform=[3, [4, float]],
            transform=mat3x4,
            mask8_idx24=_torch.int32,
            instanceShaderBindingTableRecordOffset=[3, _torch.uint8],
            flags=_torch.uint8,
            accelerationStructureReference=_torch.int64
        )

    @staticmethod
    def from_instance2():
        """
        Creates a layout for an instance buffer
        """
        return Layout.from_structure(
            mode=LayoutAlignment.SCALAR,
            # transform=[3, [4, float]],
            transform=mat3x4,
            instanceCustomIndex=[3, _torch.uint8],
            mask=_torch.uint8,
            instanceShaderBindingTableRecordOffset=[3, _torch.uint8],
            flags=_torch.uint8,
            accelerationStructureReference=_torch.int64
        )


    @staticmethod
    def from_aabb():
        """
        Creates a layout for an axis-aligned boundary boxes buffer.
        """
        return Layout.from_structure(
            mode=LayoutAlignment.SCALAR,
            b_min=vec3,
            b_max=vec3
        )

    @staticmethod
    def set_24bit_from_int(src: _torch.Tensor, dst: _torch.Tensor):
        """
        Copies the lowest 24 bits from integers in src to dst.
        src is a tensor of int32 and dst is a tensor of int8 with 3 components for the last dimension.
        """
        dst[...,0] = src % 256
        dst[...,1] = (src >> 8) % 256
        dst[...,2] = (src >> 16) % 256

    @staticmethod
    def is_scalar_type(type):
        if type == float or type == int:
            return True
        return isinstance(type, _typing.Hashable) and type in Layout.__TYPE_SIZES__

    __FORMAT_TO_TORCH_INFO__ = {
        Format.NONE: (1, _torch.uint8),
        Format.UINT_RGBA: (4, _torch.uint8),
        Format.UINT_RGB: (3, _torch.uint8),
        Format.UINT_BGRA_STD: (4, _torch.uint8),
        Format.UINT_RGBA_STD: (4, _torch.uint8),
        Format.UINT_RGBA_UNORM: (4, _torch.uint8),
        Format.UINT_BGRA_UNORM: (4, _torch.uint8),
        Format.FLOAT: (1, _torch.float32),
        Format.INT: (1, _torch.int32),
        Format.UINT: (1, _torch.int32),
        Format.VEC2: (2, _torch.float32),
        Format.VEC3: (3, _torch.float32),
        Format.VEC4: (4, _torch.float32),
        Format.IVEC2: (2, _torch.int32),
        Format.IVEC3: (3, _torch.int32),
        Format.IVEC4: (4, _torch.int32),
        Format.UVEC2: (2, _torch.int32),
        Format.UVEC3: (3, _torch.int32),
        Format.UVEC4: (4, _torch.int32),
        Format.PRESENTER: (4, _torch.uint8),
        Format.DEPTH_STENCIL: (4, _torch.uint8),
    }

    @staticmethod
    def from_format(format: Format):
        """
        Creates a layout for the image formats.
        """
        components, dtype = Layout.__FORMAT_TO_TORCH_INFO__[format]
        return Layout._build_layout_scalar([components, dtype])


class StructuredTensor(object):
    """
    Allows to access the memory of a tensor assuming a specific layout.
    """
    @staticmethod
    def _create_tensor_map(byte_tensor: _torch.Tensor, element_layout: Layout):
        added_shape = []
        while element_layout.is_array:
            added_shape.append(element_layout.size // element_layout.stride)
            element_layout = element_layout.element_layout
        if len(added_shape) > 0:
            byte_tensor = byte_tensor.view(*byte_tensor.shape[:-1], *added_shape, -1)
        byte_tensor = byte_tensor[..., 0:element_layout.size]
        if element_layout.is_structure:
            return byte_tensor
        if element_layout.is_scalar:
            return ViewTensor.reinterpret(byte_tensor, element_layout.declaration)
        # In this point only vector and matrix possibility left
        vector_layout = element_layout if element_layout.is_vector else element_layout.element_layout
        component_type = vector_layout.element_layout.declaration
        scalar_tensor = ViewTensor.reinterpret(byte_tensor, component_type)
        if element_layout.is_vector:
            return element_layout.declaration()(scalar_tensor)
        matrix_type: GTensorMeta = element_layout.declaration
        matrix_tensor = scalar_tensor.view(*scalar_tensor.shape[:-1], matrix_type.tensor_shape[0], -1)
        return element_layout.declaration(matrix_tensor[..., 0:matrix_type.tensor_shape[1]])

    @staticmethod
    def create_compatible_tensor(element_layout: 'Layout', *structure_shape: int, device: _typing.Union[_torch.device, str, None] = None):
        required_alignment = element_layout.alignment
        if required_alignment <= 1:
            nbytes = 1
            dtype = _torch.uint8
        elif required_alignment <= 2:
            nbytes = 2
            dtype = _torch.short
        elif required_alignment <= 4:
            nbytes = 4
            dtype = _torch.int32
        elif required_alignment <= 8:
            nbytes = 8
            dtype = _torch.int64
        else:
            nbytes = 16
            dtype = _torch.complex128
        n = (element_layout.size + nbytes - 1) // nbytes * nbytes
        t = _torch.zeros(*structure_shape, n, dtype=dtype, device=device)
        return ViewTensor.reinterpret(t, _torch.uint8)[..., 0:element_layout.aligned_size]

    @staticmethod
    def create(layout: Layout, device: _typing.Union[_torch.device, str, None] = None) -> 'StructuredTensor':
        if device is None:
            device = 'cpu'
        tensor = StructuredTensor.create_compatible_tensor(layout, device=device)
        return StructuredTensor(tensor, layout)

    def __init__(self, base_tensor: _torch.Tensor, element_layout: Layout):
        import math
        if base_tensor.dtype != _torch.uint8:
            base_tensor = ViewTensor.reinterpret(base_tensor, _torch.uint8)
        assert base_tensor.shape[-1] >= element_layout.size, "Byte size of the last dimension is not sufficient for the layout structure"
        if base_tensor.shape[-1] > element_layout.size:
            base_tensor = base_tensor[...,0:element_layout.size]
        object.__setattr__(self, '_can_copy_direct', math.prod(base_tensor.shape[:-1]) == 1 and base_tensor.device.type=='cpu')
        assert all(
            (base_tensor.data_ptr() + base_tensor.stride(d)) % element_layout.alignment == 0
            for d in range(len(base_tensor.shape)-1)
        ), 'Wrong alignment wrt layout structure in some dimension'
        object.__setattr__(self, 'base_tensor', base_tensor)
        object.__setattr__(self, 'layout', element_layout)
        if element_layout.is_compact:
            object.__setattr__(self, 'map_tensor', base_tensor)
        else:
            object.__setattr__(self, 'map_tensor', StructuredTensor._create_tensor_map(base_tensor, element_layout))
        object.__setattr__(self, '_cached_fields', dict())

    def __getitem__(self, item):
        return StructuredTensor(self.base_tensor.__getitem__(item), self.layout)

    def numpy(self):
        return self.base_tensor.numpy()

    def tensor(self):
        """
        If layout is compatible with torch, returns a tensor sharing the same memory
        """
        return self.map_tensor

    def structure_shape(self):
        return self.base_tensor.shape[:-1]

    def read(self, out: _torch.Tensor = None):
        """
        For basic types (scalars, vectors and matrices), returns a tensor with the request values.
        For vectors and matrices a GTensor is returned. If out tensor is provided, copy elements to that tensor
        """
        if out is None:
            out = StructuredTensor.create_compatible_tensor(self.layout, self.structure_shape(), self.base_tensor.device)
        if out.dtype != _torch.uint8:
            out = ViewTensor.reinterpret(out, _torch.uint8)
        if out.shape[-1] > self.layout.size:
            out = out[...,0:self.layout.size]
        assert out.shape == self.base_tensor.shape, 'Incompatible size of tensor out'
        out.copy_(self.base_tensor)
        return StructuredTensor._create_tensor_map(out, self.layout)

    def write(self, data: _torch.Tensor):
        """
        Writes the content of data to this structure tensor.
        """
        if data.dtype != _torch.uint8:
            data = ViewTensor.reinterpret(data, _torch.uint8)
        assert data.shape == self.base_tensor.shape, 'Incompatible size of tensor data'
        self.base_tensor.copy_(data)

    def get_field(self, name: str) -> 'StructuredTensor':
        assert self.layout.is_structure or self.layout.is_vector(), 'Fields can only be asked for vectors and structures'
        if self.layout.is_structure:
            offset, layout = self.layout.fields_layout[name]
            field_tensor = self.base_tensor[..., offset:offset + layout.size]
            return StructuredTensor(field_tensor, layout)
        else:  # self.layout.is_vector():
            components = {'x': 0, 'y': 1, 'z': 2, 'w': 3}
            assert name in components, 'Invalid vector component name'
            component_index = components[name]
            assert component_index < self.layout.declaration().tensor_shape[-1], 'Component index exceed vector arity'
            field_size = self.layout.element_layout.size
            offset = component_index * field_size
            field_tensor = self.base_tensor[..., offset:offset + field_size]
            return StructuredTensor(field_tensor, self.layout.element_layout)

    def get_item(self, *index: int) -> 'StructuredTensor':
        assert len(index) > 0, 'No index specified'
        current_index = index[0]
        next_indices = index[1:]
        if self.layout.is_array:
            array_len = self.layout.declaration()[0]
            assert current_index < array_len
            item_start = self.layout.stride * current_index
            item_end = item_start + self.layout.stride
            item_tensor = self.base_tensor[..., item_start:item_end]
            element_layout = self.layout.element_layout
            item_structured_tensor = StructuredTensor(item_tensor, element_layout)
            if len(next_indices) == 0:
                if element_layout.is_scalar or element_layout.is_vector() or element_layout.is_matrix():
                    return item_structured_tensor.tensor()
                return item_structured_tensor
            return item_structured_tensor.get_item(*next_indices)
        array_len = self.layout.declaration().tensor_shape[0]
        assert current_index < array_len
        if self.layout.is_vector():
            component_size = self.layout.element_layout.size
            item_start = component_size * current_index
            item_end = item_start + component_size
            item_tensor = self.base_tensor[..., item_start:item_end]
            item_structured_tensor = StructuredTensor(item_tensor, self.layout.element_layout)
            assert len(next_indices) == 0, 'Can not index a vector with more than one index'
            return item_structured_tensor
        if self.layout.is_matrix():
            vector_size = self.layout.element_layout.size
            vector_aligned_size = self.layout.element_layout.aligned_size
            item_start = vector_aligned_size * current_index
            item_end = item_start + vector_size
            item_tensor = self.base_tensor[..., item_start:item_end]
            item_structured_tensor = StructuredTensor(item_tensor, self.layout.element_layout)
            assert len(next_indices) <= 1, 'Can not index a matrix with more than two indices'
            if len(next_indices) == 0:
                return item_structured_tensor
            return item_structured_tensor.item(*next_indices)
        assert False, 'Can only index vector, matrices or arrays'

    def _direct_if_possible(self, s: _typing.Union[_torch.Tensor, 'StructuredTensor']) -> _typing.Union['StructuredTensor', _torch.Tensor, GTensorMeta]:
        if isinstance(s, _torch.Tensor):
            return s
        if s.layout.is_structure or s.layout.is_array:
            return s
        return s.tensor()

    def __getattr__(self, item):
        if item not in self._cached_fields:
            self._cached_fields[item] = self._direct_if_possible(self.get_field(item))
        return self._cached_fields[item]

    def __setattr__(self, key, value):
        assert self.layout.is_structure or self.layout.is_vector(), 'Can not update field values for non-structure or non-vector'
        # if self._can_copy_direct:
        #     offset, layout = self.layout.get_field_offset_and_layout(key)
        #     if layout.aligned_size == layout.size or layout.is_vector():  # continuous in memory.
        #         dst_ptr = self.base_tensor.data_ptr() + offset
        #         value_is_tensor = isinstance(value, _torch.Tensor)
        #         if value_is_tensor and value.device.type == 'cpu':
        #             src_ptr = value.data_ptr()
        #             _ctypes.memmove(dst_ptr, src_ptr, layout.size)
        #             return
        #         if not value_is_tensor:
        #             c_value = layout.get_ctype()(value)
        #             src_ptr = _ctypes.cast(_ctypes.pointer(c_value), _ctypes.c_void_p).value
        #             _ctypes.memmove(dst_ptr, src_ptr, layout.size)
        #             return
        if key not in self._cached_fields:
            self._cached_fields[key] = self._direct_if_possible(self.get_field(key))
        self._cached_fields[key].as_subclass(_torch.Tensor)[:] = value
        # offset, layout = self.layout.get_field_offset_and_layout(key)
        # field_tensor = self.base_tensor[..., offset:offset + layout.size]
        # if isinstance(value, _torch.Tensor):
        #     ViewTensor.reinterpret(field_tensor, value.dtype)[:] = value
        # else:
        #     ViewTensor.reinterpret(field_tensor, layout.get_type())[:] = value

    def __call__(self, *index: int) -> 'StructuredTensor':
        return self._direct_if_possible(self.get_item(*index))



