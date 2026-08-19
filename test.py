import sys

def main():
    data = sys.stdin.read().strip().split()

    if len(data) != 2:
        print("Please provide exactly two integers.", file=sys.stderr)
        raise SystemExit(1)

    a, b = map(int, data)
    print(a + b)

if __name__ == "__main__":
    main()
    