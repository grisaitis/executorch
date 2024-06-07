# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import operator

import torch
from executorch.exir.dialects._ops import ops as exir_ops
from executorch.exir.pass_base import ExportPass, PassResult
from torch._subclasses.fake_tensor import FakeTensor


class I64toI32(ExportPass):

    def _kwargs_to_int32(self, kwargs):
        new_kwargs = {"dtype": torch.int32}
        for k, v in kwargs.items():
            if k != "dtype":
                new_kwargs[k] = v
        return new_kwargs

    def _apply_to_int32(self, graph: torch.fx.Graph):
        for node in graph.nodes:
            # For each i64 dtype input, append a _to_dim_order_copy node to convert to i32 dtype.
            if node.op == "placeholder":
                node_val = node.meta["val"]
                if isinstance(node_val, FakeTensor) and node_val.dtype == torch.int64:
                    with graph.inserting_after(node):
                        args = (node,)
                        node_i32 = graph.create_node(
                            "call_function",
                            exir_ops.edge.dim_order_ops._to_dim_order_copy.default,
                            args,
                            {
                                "dtype": torch.int32,
                                "dim_order": list(range(node_val.ndim)),
                            },
                        )
                        # Replace all uses except the input to i32.
                        node.replace_all_uses_with(node_i32)
                        node_i32.args = (node,)
                        node_i32.meta["val"] = node_val.to(torch.int32)

            # For each operation yielding i64 dtype, replace with 32 dtype. 
            if node.op == "call_function" and node.target != operator.getitem:
                node_val = node.meta["val"]
                if isinstance(node_val, FakeTensor) and node_val.dtype == torch.int64:
                    node.meta["val"] = node.meta["val"].to(torch.int32)
                    for schema_arg in node.target._schema.arguments:
                        if schema_arg.name == "dtype":
                            node.kwargs = self._kwargs_to_int32(node.kwargs)
                            break

            # For each i64 dtype output, prepend a _to_dim_order_copy node to convert to i32 dtype.
            if node.op == "output":
                for i, node_val in enumerate(node.meta["val"]):
                    if (
                        isinstance(node_val, FakeTensor)
                        and node_val.dtype == torch.int64
                    ):
                        with graph.inserting_before(node):
                            args = (node.args[0][i],)
                            node_i64 = graph.create_node(
                                "call_function",
                                exir_ops.edge.dim_order_ops._to_dim_order_copy.default,
                                args,
                                {
                                    "dtype": torch.int64,
                                    "dim_order": list(range(node_val.ndim)),
                                },
                            )
                            node_args_list = list(node.args[0])
                            node_args_list[i] = node_i64
                            node.args = (tuple(node_args_list),)
                            node_i64.meta["val"] = node_val

    def call(self, graph_module: torch.fx.GraphModule) -> PassResult:
        self._apply_to_int32(graph_module.graph)
        return PassResult(graph_module, True)
