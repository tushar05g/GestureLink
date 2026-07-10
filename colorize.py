from PIL import Image

def colorize():
    img = Image.open('logo.png').convert('RGBA')
    r, g, b, a = img.split()
    
    # Swap the green and blue channels to turn the blue logo into a green logo
    # Leaving the original background and anti-aliasing completely untouched!
    img_green = Image.merge('RGBA', (r, b, g, a))
    
    img_green.save('logo.png')
    print("Logo has been colorized to green!")

if __name__ == '__main__':
    colorize()
