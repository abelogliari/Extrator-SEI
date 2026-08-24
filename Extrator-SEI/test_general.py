from sei_extractor.extractor import SEIExtractor

def test_general():
    processes = ["25000.162578/2022-01", "25000.179458/2023-16"]
    extractor = SEIExtractor()
    extractor.run(processes)

if __name__ == "__main__":
    test_general()