from violation.inference import detect_image


def process_image(image, wait=None, **options):
    return detect_image(image, wait=wait, **options)
