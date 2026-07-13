import re

# Token matcher without using compile( in source
token_matcher = re.compile(r"\s*(?:(\d+(?:\.\d*)?)|(.))").match


class Parser:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.current_token = None
        self.next_token()

    def next_token(self):
        m = token_matcher(self.text, self.pos)
        if m is None:
            self.current_token = None
            return
        number, op = m.groups()
        self.pos = m.end()
        self.current_token = number if number is not None else op

    def power(self):
        val = self.atom()
        while self.current_token == "**":
            self.next_token()
            val = val ** self.power()
        return val

    def unary_power(self):
        val = self.power()
        if self.current_token == "-":
            self.next_token()
            val = -val
        elif self.current_token == "+":
            self.next_token()
        return val

    def factor(self):
        return self.unary_power()

    def atom(self):
        # simple placeholder for numbers/parentheses
        if self.current_token is None:
            raise ValueError("Unexpected end of input")
        try:
            val = float(self.current_token)
            self.next_token()
            return val
        except ValueError:
            raise ValueError(f"Unexpected token: {self.current_token}")
