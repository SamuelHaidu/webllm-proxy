import re


class Token:
    NUMBER, OP, LPAREN, RPAREN, EOF = range(5)

    def __init__(self, type_, value=None):
        self.type = type_
        self.value = value


class Lexer:
    token_specification = [
        ("NUMBER", r"\d*\.?\d+"),
        ("OP", r"\*\*|[+\-*/%]"),
        ("LPAREN", r"\("),
        ("RPAREN", r"\)"),
        ("SKIP", r"[ \t]+"),
        ("MISMATCH", r"."),
    ]

    def __init__(self, text):
        self.text = text
        self.tokens = self.tokenize(text)
        self.pos = 0

    def tokenize(self, text):
        tok_regex = "|".join(f"(?P<{name}>{pattern})" for name, pattern in self.token_specification)
        get_token = re.compile(tok_regex).match
        pos = 0
        tokens = []
        while pos < len(text):
            m = get_token(text, pos)
            if not m:
                raise ValueError(f"Unexpected character: {text[pos]}")
            typ = m.lastgroup
            val = m.group(typ)
            if typ == "NUMBER":
                if val.count(".") > 1:
                    raise ValueError(f"Malformed number: {val}")
                tokens.append(Token(Token.NUMBER, float(val)))
            elif typ == "OP":
                tokens.append(Token(Token.OP, val))
            elif typ == "LPAREN":
                tokens.append(Token(Token.LPAREN, val))
            elif typ == "RPAREN":
                tokens.append(Token(Token.RPAREN, val))
            elif typ == "MISMATCH":
                raise ValueError(f"Unknown character: {val}")
            pos = m.end()
        tokens.append(Token(Token.EOF))
        return tokens

    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        self.pos += 1
        return self.tokens[self.pos - 1]


class Parser:
    def __init__(self, lexer):
        self.lexer = lexer

    def parse(self):
        if self.lexer.peek().type == Token.EOF:
            raise ValueError("Empty expression")
        value = self.expr()
        if self.lexer.peek().type != Token.EOF:
            raise ValueError("Unexpected token after expression")
        return value

    def expr(self):  # + -
        value = self.term()
        while self.lexer.peek().type == Token.OP and self.lexer.peek().value in ("+", "-"):
            op = self.lexer.advance().value
            right = self.term()
            if op == "+":
                value += right
            else:
                value -= right
        return value

    def term(self):  # * / %
        value = self.factor()
        while self.lexer.peek().type == Token.OP and self.lexer.peek().value in ("*", "/", "%"):
            op = self.lexer.advance().value
            right = self.factor()
            if op == "*":
                value *= right
            elif op == "/":
                if right == 0:
                    raise ZeroDivisionError("division by zero")
                value /= right
            else:  # %
                if right == 0:
                    raise ZeroDivisionError("modulo by zero")
                value = value % right
        return value

    def factor(self):  # handle exponentiation with correct precedence
        return self.power()

    def unary(self):  # unary + - binds looser than **
        if self.lexer.peek().type == Token.OP and self.lexer.peek().value in ("+", "-"):
            op = self.lexer.advance().value
            val = self.unary()
            return val if op == "+" else -val
        else:
            return self.atom()

    def power(self):  # ** right-associative
        value = self.unary()  # handle unary before **
        if self.lexer.peek().type == Token.OP and self.lexer.peek().value == "**":
            self.lexer.advance()
            exponent = self.power()  # right-associative
            value = value**exponent
        return value

    def atom(self):
        tok = self.lexer.peek()
        if tok.type == Token.NUMBER:
            self.lexer.advance()
            return tok.value
        elif tok.type == Token.LPAREN:
            self.lexer.advance()
            if self.lexer.peek().type == Token.RPAREN:
                raise ValueError("Empty parentheses")
            val = self.expr()
            if self.lexer.peek().type != Token.RPAREN:
                raise ValueError("Unmatched parenthesis")
            self.lexer.advance()
            return val
        else:
            raise ValueError(f"Unexpected token: {tok.value}")


def evaluate(expression: str) -> float:
    if not expression or expression.strip() == "":
        raise ValueError("Empty expression")
    lexer = Lexer(expression)
    parser = Parser(lexer)
    return float(parser.parse())
