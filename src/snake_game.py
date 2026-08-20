import tkinter as tk
import random

class SnakeGame:
    def __init__(self, master):
        self.master = master
        self.master.title("Snake Game")
        self.master.resizable(False, False)

        self.canvas = tk.Canvas(master, width=400, height=400, bg="black")
        self.canvas.pack()

        self.grid_size = 20
        self.snake = [(100, 100), (80, 100), (60, 100)]
        self.food = self.create_food()
        self.direction = "Right"
        self.score = 0
        self.game_over = False

        self.master.bind("<KeyPress>", self.change_direction)

        self.update_game()

    def create_food(self):
        while True:
            x = random.randrange(0, 400, self.grid_size)
            y = random.randrange(0, 400, self.grid_size)
            if (x, y) not in self.snake:
                return (x, y)

    def draw(self):
        self.canvas.delete(tk.ALL)
        for x, y in self.snake:
            self.canvas.create_rectangle(x, y, x + self.grid_size, y + self.grid_size, fill="green")
        x, y = self.food
        self.canvas.create_oval(x, y, x + self.grid_size, y + self.grid_size, fill="red")
        self.canvas.create_text(50, 10, text=f"Score: {self.score}", fill="white", anchor="nw")

    def update_game(self):
        if self.game_over:
            return

        self.move_snake()
        self.check_collision()
        self.draw()

        self.master.after(100, self.update_game)

    def move_snake(self):
        head_x, head_y = self.snake[0]

        if self.direction == "Up":
            new_head = (head_x, head_y - self.grid_size)
        elif self.direction == "Down":
            new_head = (head_x, head_y + self.grid_size)
        elif self.direction == "Left":
            new_head = (head_x - self.grid_size, head_y)
        elif self.direction == "Right":
            new_head = (head_x + self.grid_size, head_y)

        self.snake.insert(0, new_head)

        if new_head == self.food:
            self.score += 1
            self.food = self.create_food()
        else:
            self.snake.pop()

    def check_collision(self):
        head_x, head_y = self.snake[0]

        if (
            head_x < 0
            or head_x >= 400
            or head_y < 0
            or head_y >= 400
            or self.snake[0] in self.snake[1:]
        ):
            self.game_over = True
            self.canvas.create_text(
                200, 200, text=f"Game Over! Score: {self.score}", fill="white", font=("Arial", 20)
            )

    def change_direction(self, event):
        if event.keysym == "Up" and self.direction != "Down":
            self.direction = "Up"
        elif event.keysym == "Down" and self.direction != "Up":
            self.direction = "Down"
        elif event.keysym == "Left" and self.direction != "Right":
            self.direction = "Left"
        elif event.keysym == "Right" and self.direction != "Left":
            self.direction = "Right"

if __name__ == "__main__":
    root = tk.Tk()
    game = SnakeGame(root)
    root.mainloop()
