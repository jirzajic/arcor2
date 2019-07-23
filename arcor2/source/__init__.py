import static_typing as st  # type: ignore
import typed_ast.ast3  # type: ignore

from arcor2.exceptions import Arcor2Exception

SCRIPT_HEADER = "#!/usr/bin/env python3\n""# -*- coding: utf-8 -*-\n\n"
validator = st.ast_manipulation.AstValidator[typed_ast.ast3](mode="strict")


class SourceException(Arcor2Exception):
    pass
