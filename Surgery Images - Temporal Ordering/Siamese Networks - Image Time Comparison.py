import tensorflow as tf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from ultralytics import YOLO
from tensorflow.keras.preprocessing.image import load_img, img_to_array
from tensorflow.keras import layers, models, applications
import matplotlib.pyplot as plt
import sys, time

print("Num GPUs Available: ", len(tf.config.list_physical_devices('GPU')))

def checkpoint(model_name, completed_training):
    # Define the ModelCheckpoint callback
    checkpoint_path = 'path/to/save/checkpoints/{model_name}_{completed_training}.h5'
    checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(
                                                                checkpoint_path,
                                                                monitor='val_loss',  # Monitor validation loss
                                                                save_best_only=True,  # Save only the best model
                                                                mode='min',  # Mode for monitoring the metric (minimize validation loss)
                                                                verbose=1  # Verbosity level
                                                            )
    
# Define a function to save the model
def save_model(model, file_path):
    model.save(file_path)
    print(f"Model saved at {file_path}")

def loading_bar(total, completed):
    progress = int((completed / total) * 100)
    toolbar_width = 100
    progress_length = int(toolbar_width * (completed / total))
    sys.stdout.write("[%s%s] %d%%" % ("=" * progress_length, " " * (toolbar_width - progress_length), progress))
    sys.stdout.flush()
    sys.stdout.write("\b" * (toolbar_width + 6))  # return to start of line, after ']'


# Load and preprocess images
def load_and_preprocess_image(image_path):
    img = load_img(image_path, target_size=(299, 299))  # Adjust the target size based on your requirements
    img_array = img_to_array(img) / 255.0  # Normalize the pixel values
    return img_array

# Define a custom data generator
def data_generator(image_paths_1, image_paths_2, labels, batch_size=32):
    num_samples = len(labels)
    while True:
        for offset in range(0, num_samples, batch_size):
            batch_paths_1 = image_paths_1[offset:offset+batch_size]
            batch_paths_2 = image_paths_2[offset:offset+batch_size]
            batch_labels = labels[offset:offset+batch_size]

            batch_images_1 = [load_and_preprocess_image(image_path) for image_path in batch_paths_1]
            batch_images_2 = [load_and_preprocess_image(image_path) for image_path in batch_paths_2]

            yield {
                        'input_1': [np.array(batch_images_1), np.array(batch_paths_1)],
                        'input_2': [np.array(batch_images_2), np.array(batch_paths_2)]
                    }, np.array(batch_labels)


# Read a results object class tensor and spit out a tensor of fixed length which represents frequency of each class
def class_tensor_frequency(class_tensor, length = 7):
    frequency = {i: 0 for i in range(length)}  # Initialize frequencies for each class label
    for cls in class_tensor:
        if cls in frequency:  # Ensure the class label exists in the frequency dictionary
            frequency[cls] += 1
    # Convert the frequency dictionary values to a tensor
    frequency_tensor = tf.constant([frequency[i] for i in range(length)], dtype=tf.int32)
    return frequency_tensor


def custom_fit(siamese_model, history, yolo_model, num_epochs=10, steps_per_epoch=100, batch_size = 64, patience = 3):
    optimizer = tf.keras.optimizers.Adam()
    loss_fn = tf.keras.losses.BinaryCrossentropy()
    for epoch in range(num_epochs):
        print(f"Training Epoch {epoch + 1}/{num_epochs}")
        per_epoch_history = {'loss': [], 'accuracy': [], 'val_loss': [], 'val_accuracy': []}  # Create an empty history dictionary
        train_data_gen = data_generator(train_image_paths_1, train_image_paths_2, train_labels, batch_size)
        test_data_gen = data_generator(test_image_paths_1, test_image_paths_2, test_labels, batch_size)
        #Training loop
        for step in range(steps_per_epoch):
            loading_bar(steps_per_epoch, step)
            inputs, labels = next(test_data_gen)
            
            input_1_images, input_1_paths = inputs['input_1']
            input_2_images, input_2_paths = inputs['input_2']

            # Load and preprocess images for YOLO input
            yolo_input_1 = [load_img(image_path) for image_path in input_1_paths]
            yolo_input_2 = [load_img(image_path) for image_path in input_2_paths]
            
            # Make YOLO predictions
            yolo_output_1 = yolo_model.predict(yolo_input_1, save=False, imgsz=640, conf=0.35, verbose = False)
            yolo_output_2 = yolo_model.predict(yolo_input_2, save=False, imgsz=640, conf=0.35, verbose = False)

            num1 = []
            num2 = []

            # convert the yolo output to a length tensor
            for idx in range(batch_size):
                class_tensor_1 = yolo_output_1[idx].boxes.cls
                class_tensor_2 = yolo_output_2[idx].boxes.cls
                
                num1.append(class_tensor_frequency(class_tensor_1))
                num2.append(class_tensor_frequency(class_tensor_2))

            # Train the siamese model
            with tf.GradientTape() as tape:
                num1 = np.array(num1)
                num2 = np.array(num2)
                labels = [[i] for i in labels]
                outputs = siamese_model([input_1_images, input_2_images, num1, num2])
                loss = loss_fn(labels, outputs)
                # Calculate accuracy
                predicted_labels = [1 if output >= 0.5 else 0 for output in outputs]
                true_labels = [label[0] for label in labels]
                accuracy = np.mean(np.array(predicted_labels) == np.array(true_labels))
                # Compute gradients and update weights
                gradients = tape.gradient(loss, siamese_model.trainable_variables)
                optimizer.apply_gradients(zip(gradients, siamese_model.trainable_variables))
                per_epoch_history['loss'].append(loss)
                per_epoch_history['accuracy'].append(accuracy)
 
        # Validation loop, I want to see each step, so to make the graph smooth I will recaulte batch size and hold the number of val_steps+per_epoch constant
        val_steps_per_epoch = len(test_image_paths_1) //  batch_size # Adjust for your validation data length
        print(f"Validation Epoch {epoch + 1}/{num_epochs}")
        for val_step in range(val_steps_per_epoch):
            loading_bar(val_steps_per_epoch, val_step)
            val_inputs, val_labels = next(test_data_gen)
            input_1_images, input_1_paths = val_inputs['input_1']
            input_2_images, input_2_paths = val_inputs['input_2']

            # Load and preprocess images for YOLO input
            yolo_input_1 = [load_img(image_path) for image_path in input_1_paths]
            yolo_input_2 = [load_img(image_path) for image_path in input_2_paths]
            
            # Make YOLO predictions
            yolo_output_1 = yolo_model.predict(yolo_input_1, save=False, imgsz=640, conf=0.35, verbose = False)
            yolo_output_2 = yolo_model.predict(yolo_input_2, save=False, imgsz=640, conf=0.35, verbose = False)

            num1 = []
            num2 = []

            # convert the yolo output to a length tensor
            for idx in range(batch_size):
                num1.append(class_tensor_frequency(yolo_output_1[idx].boxes.cls))
                num2.append(class_tensor_frequency(yolo_output_2[idx].boxes.cls))
            
            # We are not training the model here, just validating it
            num1 = np.array(num1)
            num2 = np.array(num2)
            val_labels = [[i] for i in val_labels]
            outputs = siamese_model([input_1_images, input_2_images, num1, num2])
            val_loss = loss_fn(val_labels, outputs)
            # Calculate accuracy
            predicted_labels = [1 if output >= 0.5 else 0 for output in outputs]
            true_labels = [label[0] for label in val_labels]
            val_accuracy = np.mean(np.array(predicted_labels) == np.array(true_labels))
            per_epoch_history['val_loss'].append(val_loss)
            per_epoch_history['val_accuracy'].append(val_accuracy)
        # Display loss and accuracy
        history['loss'].append(sum(per_epoch_history['loss']) / len(per_epoch_history['loss']))
        history['val_loss'].append(sum(per_epoch_history['val_loss']) / len(per_epoch_history['val_loss']))
        history['accuracy'].append(sum(per_epoch_history['accuracy']) / len(per_epoch_history['accuracy']))
        history['val_accuracy'].append(sum(per_epoch_history['val_accuracy']) / len(per_epoch_history['val_accuracy']))
        print(f"Epoch {epoch}/{num_epochs}, Loss: {history['loss']:.4f}, Validation Loss: {history['val_loss']:.4f}, Accuracy: {history['accuracy']:.4f}, Validation Accuracy: {history['val_accuracy']:.4f}")
        # Check for early stopping
        if epoch >= patience:  # Check if current epoch is greater than or equal to patience
            recent_val_losses = history['val_loss'][-(patience + 1):]
            if all(recent_val_losses[i] > recent_val_losses[i + 1] for i in range(patience)):
                print(f"Stopping early as validation loss didn't improve for {patience} epochs.")
                break
    return history, siamese_model

def create_siamese_model(input_shape):
    input_1 = tf.keras.Input(shape=input_shape)
    input_2 = tf.keras.Input(shape=input_shape)
    input_num1 = tf.keras.Input(shape=(1))  # Shape can be adjusted based on your data
    input_num2 = tf.keras.Input(shape=(1))  # Shape can be adjusted based on your data

    # Load a pre-trained image classification network as the shared subnetwork
    base_network = applications.InceptionV3(weights='imagenet', include_top=False, input_shape=input_shape, pooling='max')
    base_network.trainable = False  # Freeze the pre-trained weights

    output_1 = base_network(input_1)
    output_2 = base_network(input_2)

    # Measure the similarity of the two outputs
    distance = layers.Lambda(lambda x: tf.abs(x[0] - x[1]))([output_1, output_2])

    # Concatenate the outputs and YOLO detections
    combined = tf.concat([output_1, output_2, input_num1, input_num2], axis=1)
    output_dense = layers.Dense(512, activation='relu')(combined)
    output_dense = layers.Dense(256, activation='relu')(output_dense)
    outputs = layers.Dense(1, activation='sigmoid')(output_dense)

    siamese_model = tf.keras.Model(inputs=[input_1, input_2, input_num1, input_num2], outputs=outputs)
    return siamese_model




if __name__ == '__main__':
    # Read the CSV file
    csv_file_path = './data.json'
    data = pd.read_json(csv_file_path)

    # Define data_generator inputs
    train = data['train']
    test = data['test']
    train_image_paths_1 = train['img_path_1']
    train_image_paths_2 = train['img_path_2']
    train_labels = train['labels']
    test_image_paths_1 = test['img_path_1']
    test_image_paths_2 = test['img_path_2']
    test_labels = test['labels']

    # Setting up some of the constants
    training_length = len(test_image_paths_1)
    batch_size = 32
    print("training length: ", len(train_image_paths_1), "testing length: ", len(test_image_paths_2))

    # Compile the model
    input_shape = (299, 299, 3)  # Adjust the input shape based on your images

    yolo_model = YOLO("best.pt")
    siamese_model = create_siamese_model(input_shape)
    completed = False
    history = {'loss': [], 'accuracy': [], 'val_loss': [], 'val_accuracy': []}  # Create an empty history dictionary
    try:
        siamese_model = custom_fit(siamese_model, history, yolo_model, num_epochs = 30, steps_per_epoch=training_length//batch_size, batch_size = batch_size)
        completed = True
    except KeyboardInterrupt:
        
        # If KeyboardInterrupt (Ctrl+C) is detected, save the model which is passed by reference not passed by value
        save_model(siamese_model, 'model_interrupted.h5')
        print("Training interrupted. Model saved.")

        # Plotting the training and validation loss
        plt.figure(figsize=(10, 5))
        plt.subplot(1, 2, 1)
        plt.plot(history['loss'], label='Training Loss')
        plt.plot(history['val_loss'], label='Validation Loss')
        plt.title('Training and Validation Loss')
        plt.xlabel('Epoch')
        plt.ylabel('Loss')
        plt.legend()

        # Plotting the training and validation accuracy
        plt.subplot(1, 2, 2)
        plt.plot(history['accuracy'], label='Training Accuracy')
        plt.plot(history['val_accuracy'], label='Validation Accuracy')
        plt.title('Training and Validation Accuracy')
        plt.xlabel('Epoch')
        plt.ylabel('Accuracy')
        plt.legend()

        plt.tight_layout()
        plt.show()


    if completed:
        # save the model when you are done with training it
        save_model(siamese_model, 'temporal_ordering_model_trained.h5')

        # Plotting the training and validation loss
        plt.figure(figsize=(10, 5))
        plt.subplot(1, 2, 1)
        plt.plot(history['loss'], label='Training Loss')
        plt.plot(history['val_loss'], label='Validation Loss')
        plt.title('Training and Validation Loss')
        plt.xlabel('Epoch')
        plt.ylabel('Loss')
        plt.legend()

        # Plotting the training and validation accuracy
        plt.subplot(1, 2, 2)
        plt.plot(history['accuracy'], label='Training Accuracy')
        plt.plot(history['val_accuracy'], label='Validation Accuracy')
        plt.title('Training and Validation Accuracy')
        plt.xlabel('Epoch')
        plt.ylabel('Accuracy')
        plt.legend()

        plt.tight_layout()
        plt.show()

